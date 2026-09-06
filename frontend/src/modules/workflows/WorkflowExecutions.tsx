import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Card,
  Table,
  Button,
  Space,
  Tag,
  Select,
  Row,
  Col,
  Progress,
  Badge,
  message,
  Descriptions,
  Timeline,
  Collapse,
  Typography,
  Tooltip,
  Popconfirm,
} from 'antd';
import {
  ArrowLeftOutlined,
  ReloadOutlined,
  StopOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  listWorkflowExecutions,
  getWorkflowExecution,
  cancelWorkflowExecution,
  listWorkflows,
  WorkflowExecution,
  StepExecution,
  Workflow,
} from 'services/workflows';
import { ExecutionLogViewer } from './components';

const { Text, Paragraph } = Typography;
const { Panel } = Collapse;

interface WorkflowExecutionsProps {
  workflowId?: string;
  onBack?: () => void;
}

const statusConfig: Record<string, { color: string; icon: React.ReactNode }> = {
  completed: { color: 'success', icon: <CheckCircleOutlined /> },
  running: { color: 'processing', icon: <SyncOutlined spin /> },
  pending: { color: 'warning', icon: <ClockCircleOutlined /> },
  failed: { color: 'error', icon: <CloseCircleOutlined /> },
  cancelled: { color: 'default', icon: <StopOutlined /> },
  paused: { color: 'warning', icon: <ExclamationCircleOutlined /> },
  skipped: { color: 'default', icon: <StopOutlined /> },
};

const WorkflowExecutions: React.FC<WorkflowExecutionsProps> = ({ workflowId, onBack }) => {
  const [executions, setExecutions] = useState<WorkflowExecution[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedExecution, setSelectedExecution] = useState<WorkflowExecution | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [filterWorkflow, setFilterWorkflow] = useState<string>(workflowId || '');
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [logViewerVisible, setLogViewerVisible] = useState(false);
  const [logViewerExecutionId, setLogViewerExecutionId] = useState<string | null>(null);
  const executionRequestId = useRef(0);
  const executionRequestInFlight = useRef<Promise<WorkflowExecution[]> | null>(null);

  const fetchExecutions = useCallback(async (silent = false) => {
    if (silent && executionRequestInFlight.current) {
      try {
        await executionRequestInFlight.current;
      } catch {
        // Retry on the next polling interval.
      }
      return;
    }
    const requestId = ++executionRequestId.current;
    if (!silent) setLoading(true);
    const previousRequest = executionRequestInFlight.current;
    if (previousRequest) {
      try {
        await previousRequest;
      } catch {
        // The latest queued request still needs a chance to refresh the list.
      }
    }
    if (requestId !== executionRequestId.current) return;

    try {
      const params: any = {};
      if (filterWorkflow) params.workflow = filterWorkflow;
      if (filterStatus) params.status = filterStatus;
      const request = listWorkflowExecutions(params);
      executionRequestInFlight.current = request;
      const data = await request;
      if (requestId !== executionRequestId.current) return;
      const items = Array.isArray(data) ? data : [];
      setExecutions(items);
      setSelectedExecution(current => {
        const updated = current && items.find(item => item.id === current.id);
        return updated ? { ...current, ...updated } : current;
      });
    } catch (err) {
      if (!silent && requestId === executionRequestId.current) {
        message.error('Failed to load executions');
      }
    } finally {
      if (requestId === executionRequestId.current) {
        executionRequestInFlight.current = null;
      }
      if (requestId === executionRequestId.current) setLoading(false);
    }
  }, [filterWorkflow, filterStatus]);

  const fetchWorkflows = useCallback(async () => {
    try {
      const data = await listWorkflows();
      setWorkflows(Array.isArray(data) ? data : []);
    } catch (err) {
      // ignore
    }
  }, []);

  useEffect(() => {
    fetchExecutions();
    return () => {
      executionRequestId.current += 1;
    };
  }, [fetchExecutions]);

  useEffect(() => {
    fetchWorkflows();
  }, [fetchWorkflows]);

  const hasRunningExecution = executions.some(
    execution => ['pending', 'running', 'paused'].includes(execution.status),
  );
  useEffect(() => {
    if (!hasRunningExecution) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await fetchExecutions(true);
      if (!cancelled) timeout = setTimeout(poll, 5000);
    };
    timeout = setTimeout(poll, 5000);
    return () => {
      cancelled = true;
      clearTimeout(timeout);
    };
  }, [hasRunningExecution, fetchExecutions]);

  const loadExecutionDetail = async (id: string) => {
    setDetailLoading(true);
    try {
      const data = await getWorkflowExecution(id);
      setSelectedExecution(data);
    } catch (err) {
      message.error('Failed to load execution details');
    } finally {
      setDetailLoading(false);
    }
  };

  const handleCancel = async (id: string) => {
    try {
      await cancelWorkflowExecution(id);
      message.success('Execution cancelled');
      fetchExecutions();
      if (selectedExecution?.id === id) {
        loadExecutionDetail(id);
      }
    } catch (err: any) {
      message.error(err.response?.data?.error || 'Failed to cancel execution');
    }
  };

  const columns: ColumnsType<WorkflowExecution> = [
    {
      title: 'Workflow',
      dataIndex: 'workflow_name',
      key: 'workflow_name',
      render: (name: string) => <strong>{name}</strong>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => {
        const config = statusConfig[status] || statusConfig.pending;
        return (
          <Badge status={config.color as any} text={status} />
        );
      },
    },
    {
      title: 'Progress',
      key: 'progress',
      width: 180,
      render: (_: any, record: WorkflowExecution) => (
        <Space direction="vertical" size={0} style={{ width: '100%' }}>
          <Progress
            percent={Math.round(record.progress_percent)}
            size="small"
            status={record.status === 'failed' ? 'exception' : record.status === 'running' ? 'active' : undefined}
          />
          <Text type="secondary" style={{ fontSize: 11 }}>
            {record.completed_steps}/{record.total_steps} steps
          </Text>
        </Space>
      ),
    },
    {
      title: 'Trigger',
      dataIndex: 'trigger_source',
      key: 'trigger_source',
      width: 120,
      render: (source: string) => <Tag>{source || 'manual'}</Tag>,
    },
    {
      title: 'Started',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 160,
      render: (date: string) => date ? new Date(date).toLocaleString() : '-',
    },
    {
      title: 'Duration',
      dataIndex: 'duration',
      key: 'duration',
      width: 100,
      render: (duration: string) => duration || '-',
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 180,
      render: (_: any, record: WorkflowExecution) => (
        <Space>
          <Tooltip title="View Logs">
            <Button
              size="small"
              icon={<FileTextOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                setLogViewerExecutionId(record.id);
                setLogViewerVisible(true);
              }}
            />
          </Tooltip>
          <Button size="small" onClick={() => loadExecutionDetail(record.id)}>
            Details
          </Button>
          {(record.status === 'running' || record.status === 'pending') && (
            <Popconfirm
              title="Cancel this execution?"
              onConfirm={() => handleCancel(record.id)}
            >
              <Button size="small" danger icon={<StopOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      {/* Header */}
      <Card style={{ marginBottom: 16 }}>
        <Row justify="space-between" align="middle">
          <Col>
            <Space>
              {onBack && (
                <Button icon={<ArrowLeftOutlined />} onClick={onBack}>
                  Back
                </Button>
              )}
              <span style={{ fontSize: 18, fontWeight: 600 }}>
                Workflow Executions
              </span>
            </Space>
          </Col>
          <Col>
            <Space>
              <Select
                placeholder="Filter by Workflow"
                value={filterWorkflow || undefined}
                onChange={setFilterWorkflow}
                style={{ width: 200 }}
                allowClear
              >
                {workflows.map(wf => (
                  <Select.Option key={wf.id} value={wf.id}>{wf.name}</Select.Option>
                ))}
              </Select>
              <Select
                placeholder="Filter by Status"
                value={filterStatus || undefined}
                onChange={setFilterStatus}
                style={{ width: 140 }}
                allowClear
              >
                <Select.Option value="completed">Completed</Select.Option>
                <Select.Option value="running">Running</Select.Option>
                <Select.Option value="pending">Pending</Select.Option>
                <Select.Option value="failed">Failed</Select.Option>
                <Select.Option value="cancelled">Cancelled</Select.Option>
              </Select>
              <Button icon={<ReloadOutlined />} onClick={() => fetchExecutions()}>
                Refresh
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Row gutter={16}>
        {/* Executions List */}
        <Col span={selectedExecution ? 14 : 24}>
          <Card title="Executions" extra={<span>{executions.length} items</span>}>
            <Table
              columns={columns}
              dataSource={executions}
              rowKey="id"
              loading={loading}
              pagination={{ pageSize: 10 }}
              size="small"
              onRow={(record) => ({
                onClick: () => loadExecutionDetail(record.id),
                style: { cursor: 'pointer' },
              })}
            />
          </Card>
        </Col>

        {/* Execution Detail */}
        {selectedExecution && (
          <Col span={10}>
            <Card
              title="Execution Details"
              loading={detailLoading}
              extra={
                <Space>
                  <Button
                    size="small"
                    icon={<FileTextOutlined />}
                    onClick={() => {
                      setLogViewerExecutionId(selectedExecution.id);
                      setLogViewerVisible(true);
                    }}
                  >
                    Full Logs
                  </Button>
                  <Button size="small" onClick={() => setSelectedExecution(null)}>
                    Close
                  </Button>
                </Space>
              }
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Workflow">
                  {selectedExecution.workflow_name}
                </Descriptions.Item>
                <Descriptions.Item label="Status">
                  <Badge
                    status={statusConfig[selectedExecution.status]?.color as any || 'default'}
                    text={selectedExecution.status}
                  />
                </Descriptions.Item>
                <Descriptions.Item label="Progress">
                  <Progress
                    percent={Math.round(selectedExecution.progress_percent)}
                    size="small"
                    style={{ width: 150 }}
                  />
                </Descriptions.Item>
                <Descriptions.Item label="Trigger">
                  {selectedExecution.trigger_source || 'manual'}
                </Descriptions.Item>
                <Descriptions.Item label="Started">
                  {selectedExecution.started_at
                    ? new Date(selectedExecution.started_at).toLocaleString()
                    : '-'}
                </Descriptions.Item>
                <Descriptions.Item label="Duration">
                  {selectedExecution.duration || '-'}
                </Descriptions.Item>
                {selectedExecution.executed_by_username && (
                  <Descriptions.Item label="Executed By">
                    {selectedExecution.executed_by_username}
                  </Descriptions.Item>
                )}
              </Descriptions>

              {selectedExecution.error_message && (
                <Card size="small" style={{ marginTop: 16, background: '#fff2f0' }}>
                  <Text type="danger">
                    <strong>Error:</strong> {selectedExecution.error_message}
                  </Text>
                </Card>
              )}

              {/* Step Executions */}
              <div style={{ marginTop: 16 }}>
                <Text strong>Step Executions</Text>
                <Timeline style={{ marginTop: 12 }}>
                  {selectedExecution.step_executions?.map((step, index) => {
                    const config = statusConfig[step.status] || statusConfig.pending;
                    return (
                      <Timeline.Item
                        key={step.id}
                        color={config.color}
                        dot={config.icon}
                      >
                        <Collapse ghost size="small">
                          <Panel
                            header={
                              <Space>
                                <Tag>{index + 1}</Tag>
                                <span>{step.step_name}</span>
                                <Tag color={config.color}>{step.status}</Tag>
                                {step.duration_seconds !== undefined && (
                                  <Text type="secondary" style={{ fontSize: 11 }}>
                                    {step.duration_seconds.toFixed(2)}s
                                  </Text>
                                )}
                              </Space>
                            }
                            key={step.id}
                          >
                            <Space direction="vertical" style={{ width: '100%' }}>
                              <div>
                                <Text type="secondary">Action: </Text>
                                <Tag>{step.action_type}</Tag>
                              </div>
                              {step.error_message && (
                                <div>
                                  <Text type="danger">Error: {step.error_message}</Text>
                                </div>
                              )}
                              {step.logs && (
                                <div>
                                  <Text type="secondary">Logs:</Text>
                                  <pre style={{
                                    background: '#f5f5f5',
                                    padding: 8,
                                    borderRadius: 4,
                                    fontSize: 11,
                                    maxHeight: 100,
                                    overflow: 'auto',
                                  }}>
                                    {step.logs}
                                  </pre>
                                </div>
                              )}
                              {step.output_data && Object.keys(step.output_data).length > 0 && (
                                <div>
                                  <Text type="secondary">Output:</Text>
                                  <pre style={{
                                    background: '#f5f5f5',
                                    padding: 8,
                                    borderRadius: 4,
                                    fontSize: 11,
                                    maxHeight: 100,
                                    overflow: 'auto',
                                  }}>
                                    {JSON.stringify(step.output_data, null, 2)}
                                  </pre>
                                </div>
                              )}
                            </Space>
                          </Panel>
                        </Collapse>
                      </Timeline.Item>
                    );
                  })}
                </Timeline>
              </div>
            </Card>
          </Col>
        )}
      </Row>

      {/* Execution Log Viewer Modal */}
      <ExecutionLogViewer
        visible={logViewerVisible}
        executionId={logViewerExecutionId}
        onClose={() => {
          setLogViewerVisible(false);
          setLogViewerExecutionId(null);
        }}
      />
    </div>
  );
};

export default WorkflowExecutions;

