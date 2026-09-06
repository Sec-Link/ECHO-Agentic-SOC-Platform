/**
 * Execution Log Viewer Component
 *
 * Provides a detailed view of workflow execution logs,
 * including step-by-step execution details, input/output data,
 * error messages, and execution logs.
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Modal,
  Tabs,
  Card,
  Timeline,
  Tag,
  Space,
  Typography,
  Descriptions,
  Input,
  Button,
  Collapse,
  Badge,
  Empty,
  Spin,
  Tooltip,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ClockCircleOutlined,
  StopOutlined,
  ExclamationCircleOutlined,
  SearchOutlined,
  CopyOutlined,
  DownloadOutlined,
  CodeOutlined,
  FileTextOutlined,
  BugOutlined,
} from '@ant-design/icons';
import { getWorkflowExecution, subscribeWorkflowProgress } from 'services/workflows';
import type { WorkflowExecution, StepExecution } from 'services/workflows';

const { Text, Paragraph } = Typography;
const { Panel } = Collapse;
const { TextArea } = Input;

interface ExecutionLogViewerProps {
  visible: boolean;
  executionId: string | null;
  onClose: () => void;
}

const statusConfig: Record<string, { color: string; icon: React.ReactNode; badgeStatus: string }> = {
  completed: { color: 'success', icon: <CheckCircleOutlined />, badgeStatus: 'success' },
  running: { color: 'processing', icon: <SyncOutlined spin />, badgeStatus: 'processing' },
  pending: { color: 'warning', icon: <ClockCircleOutlined />, badgeStatus: 'warning' },
  failed: { color: 'error', icon: <CloseCircleOutlined />, badgeStatus: 'error' },
  cancelled: { color: 'default', icon: <StopOutlined />, badgeStatus: 'default' },
  skipped: { color: 'default', icon: <StopOutlined />, badgeStatus: 'default' },
  paused: { color: 'warning', icon: <ExclamationCircleOutlined />, badgeStatus: 'warning' },
};

const ExecutionLogViewer: React.FC<ExecutionLogViewerProps> = ({
  visible,
  executionId,
  onClose,
}) => {
  const [loading, setLoading] = useState(false);
  const [execution, setExecution] = useState<WorkflowExecution | null>(null);
  const [searchText, setSearchText] = useState('');
  const [activeTab, setActiveTab] = useState('timeline');
  const executionRequestId = useRef(0);

  const loadExecution = useCallback(async (silent = false) => {
    if (!executionId) return;
    const requestId = ++executionRequestId.current;
    if (!silent) setLoading(true);
    try {
      const data = await getWorkflowExecution(executionId);
      if (requestId === executionRequestId.current) setExecution(data);
    } catch (err) {
      if (!silent && requestId === executionRequestId.current) message.error('Failed to load execution details');
    } finally {
      if (requestId === executionRequestId.current) setLoading(false);
    }
  }, [executionId]);

  useEffect(() => {
    setExecution(null);
    if (!visible || !executionId) return;
    void loadExecution();
    const unsubscribe = subscribeWorkflowProgress(progress => {
      if (!progress || progress.execution_id === executionId) void loadExecution(true);
    });
    return () => {
      executionRequestId.current += 1;
      unsubscribe();
    };
  }, [visible, executionId, loadExecution]);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    message.success('Copied to clipboard');
  };

  const downloadLogs = () => {
    if (!execution) return;

    const logContent = generateFullLog();
    const blob = new Blob([logContent], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `workflow-execution-${execution.id}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const generateFullLog = (): string => {
    if (!execution) return '';

    const lines: string[] = [
      '=' .repeat(80),
      `Workflow Execution Log`,
      '=' .repeat(80),
      '',
      `Workflow: ${execution.workflow_name}`,
      `Execution ID: ${execution.id}`,
      `Status: ${execution.status}`,
      `Trigger: ${execution.trigger_source || 'manual'}`,
      `Started: ${execution.started_at || 'N/A'}`,
      `Completed: ${execution.completed_at || 'N/A'}`,
      `Duration: ${execution.duration || 'N/A'}`,
      `Executed By: ${execution.executed_by_username || 'System'}`,
      '',
    ];

    if (execution.error_message) {
      lines.push('-'.repeat(80));
      lines.push('ERROR:');
      lines.push(execution.error_message);
      lines.push('');
    }

    lines.push('-'.repeat(80));
    lines.push('TRIGGER DATA:');
    lines.push(JSON.stringify(execution.trigger_data, null, 2));
    lines.push('');

    lines.push('='.repeat(80));
    lines.push('STEP EXECUTIONS');
    lines.push('='.repeat(80));
    lines.push('');

    execution.step_executions?.forEach((step, index) => {
      lines.push(`--- Step ${index + 1}: ${step.step_name} ---`);
      lines.push(`Action: ${step.action_type}`);
      lines.push(`Status: ${step.status}`);
      lines.push(`Started: ${step.started_at || 'N/A'}`);
      lines.push(`Duration: ${step.duration_seconds?.toFixed(2) || 'N/A'}s`);

      if (step.input_data && Object.keys(step.input_data).length > 0) {
        lines.push('Input:');
        lines.push(JSON.stringify(step.input_data, null, 2));
      }

      if (step.output_data && Object.keys(step.output_data).length > 0) {
        lines.push('Output:');
        lines.push(JSON.stringify(step.output_data, null, 2));
      }

      if (step.logs) {
        lines.push('Logs:');
        lines.push(step.logs);
      }

      if (step.error_message) {
        lines.push('Error:');
        lines.push(step.error_message);
      }

      lines.push('');
    });

    return lines.join('\n');
  };

  const filterSteps = (steps: StepExecution[] | undefined): StepExecution[] => {
    if (!steps) return [];
    if (!searchText) return steps;

    const lower = searchText.toLowerCase();
    return steps.filter(step =>
      step.step_name?.toLowerCase().includes(lower) ||
      step.action_type?.toLowerCase().includes(lower) ||
      step.logs?.toLowerCase().includes(lower) ||
      step.error_message?.toLowerCase().includes(lower) ||
      JSON.stringify(step.output_data)?.toLowerCase().includes(lower)
    );
  };

  const renderTimeline = () => {
    const steps = filterSteps(execution?.step_executions);

    if (steps.length === 0) {
      return <Empty description="No step executions found" />;
    }

    return (
      <Timeline>
        {steps.map((step, index) => {
          const config = statusConfig[step.status] || statusConfig.pending;
          return (
            <Timeline.Item
              key={step.id}
              color={config.color}
              dot={config.icon}
            >
              <Collapse ghost>
                <Panel
                  header={
                    <Space wrap>
                      <Tag color="blue">{index + 1}</Tag>
                      <Text strong>{step.step_name}</Text>
                      <Tag>{step.action_type}</Tag>
                      <Badge status={config.badgeStatus as any} text={step.status} />
                      {step.duration_seconds !== undefined && (
                        <Text type="secondary">({step.duration_seconds.toFixed(2)}s)</Text>
                      )}
                      {step.started_at && (
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {new Date(step.started_at).toLocaleString()}
                        </Text>
                      )}
                    </Space>
                  }
                  key={step.id}
                >
                  <Space direction="vertical" style={{ width: '100%' }} size="small">
                    {/* Timing Info */}
                    <Descriptions size="small" column={2}>
                      <Descriptions.Item label="Started">
                        {step.started_at ? new Date(step.started_at).toLocaleString() : '-'}
                      </Descriptions.Item>
                      <Descriptions.Item label="Completed">
                        {step.completed_at ? new Date(step.completed_at).toLocaleString() : '-'}
                      </Descriptions.Item>
                      <Descriptions.Item label="Attempt">
                        {step.attempt_number || 1}
                      </Descriptions.Item>
                      <Descriptions.Item label="Duration">
                        {step.duration_seconds?.toFixed(2) || '-'}s
                      </Descriptions.Item>
                    </Descriptions>

                    {/* Input Data */}
                    {step.input_data && Object.keys(step.input_data).length > 0 && (
                      <Card
                        size="small"
                        title={<><CodeOutlined /> Input Data</>}
                        extra={
                          <Button
                            size="small"
                            icon={<CopyOutlined />}
                            onClick={() => copyToClipboard(JSON.stringify(step.input_data, null, 2))}
                          />
                        }
                      >
                        <pre style={{
                          background: '#f5f5f5',
                          padding: 8,
                          borderRadius: 4,
                          fontSize: 11,
                          maxHeight: 150,
                          overflow: 'auto',
                          margin: 0,
                        }}>
                          {JSON.stringify(step.input_data, null, 2)}
                        </pre>
                      </Card>
                    )}

                    {/* Output Data */}
                    {step.output_data && Object.keys(step.output_data).length > 0 && (
                      <Card
                        size="small"
                        title={<><CodeOutlined /> Output Data</>}
                        style={{ borderColor: '#52c41a' }}
                        extra={
                          <Button
                            size="small"
                            icon={<CopyOutlined />}
                            onClick={() => copyToClipboard(JSON.stringify(step.output_data, null, 2))}
                          />
                        }
                      >
                        <pre style={{
                          background: '#f6ffed',
                          padding: 8,
                          borderRadius: 4,
                          fontSize: 11,
                          maxHeight: 150,
                          overflow: 'auto',
                          margin: 0,
                        }}>
                          {JSON.stringify(step.output_data, null, 2)}
                        </pre>
                      </Card>
                    )}

                    {/* Logs */}
                    {step.logs && (
                      <Card
                        size="small"
                        title={<><FileTextOutlined /> Execution Logs</>}
                        extra={
                          <Button
                            size="small"
                            icon={<CopyOutlined />}
                            onClick={() => copyToClipboard(step.logs || '')}
                          />
                        }
                      >
                        <pre style={{
                          background: '#fafafa',
                          padding: 8,
                          borderRadius: 4,
                          fontSize: 11,
                          maxHeight: 150,
                          overflow: 'auto',
                          margin: 0,
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                        }}>
                          {step.logs}
                        </pre>
                      </Card>
                    )}

                    {/* Error Message */}
                    {step.error_message && (
                      <Card
                        size="small"
                        title={<><BugOutlined /> Error</>}
                        style={{ borderColor: '#ff4d4f', background: '#fff2f0' }}
                        extra={
                          <Button
                            size="small"
                            icon={<CopyOutlined />}
                            onClick={() => copyToClipboard(step.error_message || '')}
                          />
                        }
                      >
                        <Text type="danger" style={{ fontFamily: 'monospace', fontSize: 12 }}>
                          {step.error_message}
                        </Text>
                      </Card>
                    )}
                  </Space>
                </Panel>
              </Collapse>
            </Timeline.Item>
          );
        })}
      </Timeline>
    );
  };

  const renderFullLog = () => {
    const logContent = generateFullLog();
    return (
      <div>
        <div style={{ marginBottom: 16 }}>
          <Space>
            <Button icon={<CopyOutlined />} onClick={() => copyToClipboard(logContent)}>
              Copy All
            </Button>
            <Button icon={<DownloadOutlined />} onClick={downloadLogs}>
              Download
            </Button>
          </Space>
        </div>
        <TextArea
          value={logContent}
          readOnly
          style={{
            fontFamily: 'monospace',
            fontSize: 11,
            height: 500,
            background: '#1e1e1e',
            color: '#d4d4d4',
          }}
        />
      </div>
    );
  };

  const renderContext = () => {
    if (!execution) return null;

    return (
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Card size="small" title="Trigger Data">
          <pre style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 4,
            fontSize: 12,
            maxHeight: 200,
            overflow: 'auto',
            margin: 0,
          }}>
            {JSON.stringify(execution.trigger_data, null, 2)}
          </pre>
        </Card>

        <Card size="small" title="Execution Context">
          <pre style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 4,
            fontSize: 12,
            maxHeight: 200,
            overflow: 'auto',
            margin: 0,
          }}>
            {JSON.stringify(execution.context, null, 2)}
          </pre>
        </Card>

        <Card size="small" title="Result Data">
          <pre style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 4,
            fontSize: 12,
            maxHeight: 200,
            overflow: 'auto',
            margin: 0,
          }}>
            {JSON.stringify(execution.result_data, null, 2)}
          </pre>
        </Card>
      </Space>
    );
  };

  return (
    <Modal
      title={
        <Space>
          <FileTextOutlined />
          Execution Logs
          {execution && (
            <Badge
              status={statusConfig[execution.status]?.badgeStatus as any || 'default'}
              text={execution.status}
            />
          )}
        </Space>
      }
      open={visible}
      onCancel={onClose}
      width={900}
      footer={[
        <Button key="download" icon={<DownloadOutlined />} onClick={downloadLogs}>
          Download Logs
        </Button>,
        <Button key="close" type="primary" onClick={onClose}>
          Close
        </Button>,
      ]}
    >
      <Spin spinning={loading}>
        {execution ? (
          <>
            {/* Execution Summary */}
            <Card size="small" style={{ marginBottom: 16 }}>
              <Descriptions size="small" column={3}>
                <Descriptions.Item label="Workflow">
                  <Text strong>{execution.workflow_name}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="Status">
                  <Badge
                    status={statusConfig[execution.status]?.badgeStatus as any || 'default'}
                    text={execution.status}
                  />
                </Descriptions.Item>
                <Descriptions.Item label="Progress">
                  {execution.completed_steps}/{execution.total_steps} steps ({Math.round(execution.progress_percent)}%)
                </Descriptions.Item>
                <Descriptions.Item label="Started">
                  {execution.started_at ? new Date(execution.started_at).toLocaleString() : '-'}
                </Descriptions.Item>
                <Descriptions.Item label="Duration">
                  {execution.duration || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="Trigger">
                  {execution.trigger_source || 'manual'}
                </Descriptions.Item>
              </Descriptions>

              {execution.error_message && (
                <Card
                  size="small"
                  style={{ marginTop: 12, background: '#fff2f0', borderColor: '#ff4d4f' }}
                >
                  <Space>
                    <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                    <Text type="danger" strong>Error: </Text>
                    <Text type="danger">{execution.error_message}</Text>
                  </Space>
                </Card>
              )}
            </Card>

            {/* Search */}
            <Input
              placeholder="Search in logs..."
              prefix={<SearchOutlined />}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              style={{ marginBottom: 16 }}
              allowClear
            />

            {/* Tabs */}
            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              items={[
                {
                  key: 'timeline',
                  label: 'Step Timeline',
                  children: renderTimeline(),
                },
                {
                  key: 'fulllog',
                  label: 'Full Log',
                  children: renderFullLog(),
                },
                {
                  key: 'context',
                  label: 'Context Data',
                  children: renderContext(),
                },
              ]}
            />
          </>
        ) : (
          <Empty description="No execution data" />
        )}
      </Spin>
    </Modal>
  );
};

export default ExecutionLogViewer;

