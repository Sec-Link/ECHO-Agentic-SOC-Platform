import os
import json
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Task, TaskRun
from .serializers import TaskSerializer, TaskRunSerializer, TaskRequestLogSerializer
from .models import TaskRequestLog
from integrations.views import sync_es_to_db
from integrations.models import Integration
from django.utils import timezone


GENERATED_DIR = os.path.join(settings.BASE_DIR, 'generated_tasks')
os.makedirs(GENERATED_DIR, exist_ok=True)
TASK_REQUESTS_DIR = os.path.join(settings.BASE_DIR, 'orchestrator_task_requests')
os.makedirs(TASK_REQUESTS_DIR, exist_ok=True)

# -----------------------------
# 中文注释（文件级别说明）
#
# 该模块负责调度/任务（Task）相关的 REST 接口：
# - `TaskViewSet` 提供 CRUD，创建/更新 Task 时会在磁盘上生成任务配置文件和运行脚本（存放在 generated_tasks 目录）
# - `TaskViewSet.run` 提供触发任务执行的 API（调用 orchestrator.utils.execute_task），并返回 TaskRun 记录
# - `TaskRunViewSet` 提供只读的任务运行记录查询
#
# 生成的 runner 脚本和 config 主要用于在外部运行器（例如 CI、cron 或流水线）执行任务。注意：为保持原样行为，本文件仅添加注释，未改变现有逻辑或文件写入策略。
# -----------------------------


class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all().order_by('-created_at')
    serializer_class = TaskSerializer
    DEST_TABLE = "alerts_alert"

    def _normalize_config(self, config):
        cfg = dict(config or {})
        cfg["table"] = self.DEST_TABLE
        return cfg

    def perform_create(self, serializer):
        task = serializer.save(config=self._normalize_config(serializer.validated_data.get("config")))
        # generate task config and DAG immediately
        self._generate_task_files(task)
        # persist the incoming request payload to disk for auditing/debug
        try:
            # Persist audit log to database instead of writing to disk
            TaskRequestLog.objects.create(task=task, user=str(self.request.user) if getattr(self.request, 'user', None) else None, request_body=self.request.data)
        except Exception:
            pass

    def perform_update(self, serializer):
        task = serializer.save(config=self._normalize_config(serializer.validated_data.get("config")))
        self._generate_task_files(task)
        # persist update payload
        try:
            TaskRequestLog.objects.create(task=task, user=str(self.request.user) if getattr(self.request, 'user', None) else None, request_body=self.request.data)
        except Exception:
            pass

    def _write_task_request_log(self, request_data, task: Task = None):
        try:
            ts = timezone.now().strftime('%Y%m%dT%H%M%S')
            tid = getattr(task, 'id', None)
            tname = getattr(task, 'name', None) or 'task'
            filename = f"task_request_{tid or 'new'}_{ts}.json"
            path = os.path.join(TASK_REQUESTS_DIR, filename)
            payload = {
                'logged_at': timezone.now().isoformat(),
                'user': str(self.request.user) if hasattr(self, 'request') and getattr(self.request, 'user', None) else None,
                'task_id': str(tid) if tid else None,
                'task_name': tname,
                'request_body': request_data,
            }
            # For backward compatibility keep a disk copy if configured
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            except Exception:
                # ignore disk write errors
                pass
            return path
        except Exception:
            return None

    def _generate_task_files(self, task: Task):
        # write config JSON to disk only if enabled. Task.config remains the primary persistent source.
        cfg_path = os.path.join(GENERATED_DIR, f"task_{task.id}.json")
        try:
            from django.conf import settings as _dj_settings
            write_disk = getattr(_dj_settings, 'WRITE_CONFIG_TO_DISK', False)
        except Exception:
            write_disk = False

        if write_disk:
            try:
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    json.dump(
                        {'id': str(task.id), 'name': task.name, 'type': task.task_type, 'config': task.config},
                        f,
                        indent=2,
                    )
            except Exception:
                pass

        # generate a generic task runner shell script (executes appropriate tool based on task type)
        runner_sh = os.path.join(GENERATED_DIR, f"task_runner_{task.id}.sh")
        if task.task_type == 'logstash':
            # assume Logstash is available on PATH; run logstash with generated config
            conf_path = os.path.join(GENERATED_DIR, f"logstash_{task.id}.conf")
            try:
                from django.conf import settings as _dj_settings
                write_disk = getattr(_dj_settings, 'WRITE_CONFIG_TO_DISK', False)
            except Exception:
                write_disk = False
            if write_disk:
                try:
                    with open(conf_path, 'w', encoding='utf-8') as fconf:
                        # naive rendering: for tasks with config.inputs/filters/outputs
                        cfg = task.config or {}
                        ins = cfg.get('inputs', [])
                        fil = cfg.get('filters', [])
                        outs = cfg.get('outputs', [])
                        for i in ins:
                            fconf.write(f"input {{ {i.get('type')} {{ {i.get('path','')} }} }}\n")
                        for ff in fil:
                            fconf.write(f"filter {{ {ff.get('type')} {{ {ff.get('pattern','')} }} }}\n")
                        for o in outs:
                            fconf.write(f"output {{ {o.get('type')} {{ {o.get('config','')} }} }}\n")
                except Exception:
                    pass
                runner_content = f"#!/bin/sh\necho Running logstash for task {task.id}\nlogstash -f {conf_path}\n"
            else:
                runner_content = f"#!/bin/sh\necho Running logstash for task {task.id} (no conf file written)\n"
        else:
            runner_content = (
                f"#!/bin/sh\n"
                f"echo Running task {task.id} (type: {task.task_type})\n"
                f"echo Config file: {cfg_path}\n"
                f"cat {cfg_path}\n"
            )

        try:
            from django.conf import settings as _dj_settings
            if getattr(_dj_settings, 'WRITE_CONFIG_TO_DISK', False):
                with open(runner_sh, 'w', encoding='utf-8') as fr:
                    fr.write(runner_content)
        except Exception:
            pass

    @action(detail=True, methods=['post'])
    def run(self, request, pk=None):
        task = self.get_object()
        # delegate execution to helper so scheduler and API share behavior
        try:
            from .utils import execute_task
            run = execute_task(task)
            return Response(TaskRunSerializer(run).data)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TaskRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TaskRun.objects.all().order_by('-started_at')
    serializer_class = TaskRunSerializer


class TaskRequestLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TaskRequestLog.objects.all().order_by('-logged_at')
    serializer_class = TaskRequestLogSerializer
