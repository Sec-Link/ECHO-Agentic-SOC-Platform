import React, { useState, useEffect } from 'react';
import {
  Card,
  Form,
  Input,
  Select,
  Button,
  Space,
  Row,
  Col,
  Switch,
  Collapse,
  Tag,
  message,
  Tooltip,
  Empty,
  Modal,
  InputNumber,
} from 'antd';
import {
  SaveOutlined,
  ArrowLeftOutlined,
  PlusOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  EditOutlined,
  CheckOutlined,
} from '@ant-design/icons';
import {
  getWorkflow,
  createWorkflow,
  updateWorkflow,
  getAvailableActions,
  executeWorkflow,
  Workflow,
  WorkflowStep,
  ActionInfo,
} from 'services/workflows';
import { listInterfaceEndpoints } from 'services/interfaces';
import type { InterfaceEndpoint } from 'services/interfaces';

const { TextArea } = Input;
const { Panel } = Collapse;

interface WorkflowEditorProps {
  workflowId?: string;
  onBack: () => void;
  onSaved?: (workflow: Workflow) => void;
}

const triggerTypes = [
  { value: 'manual', label: 'Manual Execution' },
  { value: 'alert', label: 'On Alert Created' },
  { value: 'ticket_created', label: 'On Ticket Created' },
  { value: 'ticket_status', label: 'On Ticket Status Change' },
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'webhook', label: 'External Webhook' },
];

const onFailureOptions = [
  { value: 'stop', label: 'Stop Workflow' },
  { value: 'continue', label: 'Continue to Next Step' },
  { value: 'retry', label: 'Retry Step' },
  { value: 'skip', label: 'Skip to Next Step' },
];

const categoryColors: Record<string, string> = {
  utility: 'blue',
  notification: 'green',
  integration: 'purple',
  enrichment: 'orange',
  containment: 'red',
};

const normalizeTags = (value: unknown): string[] => {
  if (typeof value === 'string') {
    return value.split(',').map((t) => t.trim()).filter(Boolean);
  }
  if (Array.isArray(value)) {
    return value
      .map((t) => (typeof t === 'string' ? t.trim() : String(t || '').trim()))
      .filter(Boolean);
  }
  return [];
};

const getApiErrorMessage = (err: any, fallback: string): string => {
  const data = err?.response?.data;
  if (!data) return fallback;
  if (typeof data === 'string') return data;
  if (typeof data?.detail === 'string') return data.detail;
  if (typeof data?.error === 'string') return data.error;
  const messages: string[] = [];
  const collect = (value: any) => {
    if (typeof value === 'string') messages.push(value);
    else if (Array.isArray(value)) value.forEach(collect);
    else if (value && typeof value === 'object') Object.values(value).forEach(collect);
  };
  collect(data);
  return messages[0] || fallback;
};

const WorkflowEditor: React.FC<WorkflowEditorProps> = ({ workflowId, onBack, onSaved }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [availableActions, setAvailableActions] = useState<ActionInfo[]>([]);
  const [steps, setSteps] = useState<WorkflowStep[]>([]);
  const [editingStepIndex, setEditingStepIndex] = useState<number | null>(null);
  const [stepModalVisible, setStepModalVisible] = useState(false);
  const [stepForm] = Form.useForm();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [webhookInterfaces, setWebhookInterfaces] = useState<InterfaceEndpoint[]>([]);

  const isNew = !workflowId;

  // Load available actions
  useEffect(() => {
    const loadActions = async () => {
      try {
        const actions = await getAvailableActions();
        setAvailableActions(actions);
      } catch (err) {
        message.error('Failed to load available actions');
      }
    };
    loadActions();
  }, []);

  useEffect(() => {
    const loadWebhookInterfaces = async () => {
      try {
        const data = await listInterfaceEndpoints({ interface_type: 'webhook', is_active: true });
        setWebhookInterfaces(Array.isArray(data) ? data : []);
      } catch {
        setWebhookInterfaces([]);
      }
    };
    loadWebhookInterfaces();
  }, []);

  // Load workflow if editing
  useEffect(() => {
    if (!workflowId) {
      form.setFieldsValue({
        name: '',
        description: '',
        trigger_type: 'manual',
        webhook_source_id: undefined,
        is_active: false,
        is_draft: true,
        tags: [],
      });
      setSteps([]);
      return;
    }

    const loadWorkflow = async () => {
      setLoading(true);
      try {
        const data = await getWorkflow(workflowId);
        setWorkflow(data);
        form.setFieldsValue({
          name: data.name,
          description: data.description,
          trigger_type: data.trigger_type,
          webhook_source_id: data.trigger_conditions?.webhook_source_id,
          schedule_cron: data.schedule_cron,
          is_active: data.is_active,
          is_draft: data.is_draft,
          tags: data.tags?.join(', ') || '',
        });
        setSteps(data.steps || []);
      } catch (err) {
        message.error('Failed to load workflow');
      } finally {
        setLoading(false);
      }
    };
    loadWorkflow();
  }, [workflowId, form]);

  const handleSave = async (activate: boolean = false) => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      const tags = normalizeTags(values.tags);

      const payload: Partial<Workflow> = {
        name: values.name,
        description: values.description || '',
        trigger_type: values.trigger_type,
        trigger_conditions:
          values.trigger_type === 'webhook' && values.webhook_source_id
            ? { webhook_source_id: values.webhook_source_id }
            : {},
        schedule_cron: values.schedule_cron || null,
        is_active: activate || values.is_active,
        is_draft: !activate && values.is_draft,
        tags,
        steps: steps.map((step, index) => ({
          ...step,
          order: index,
        })),
      };

      let savedWorkflow: Workflow;
      if (isNew) {
        savedWorkflow = await createWorkflow(payload);
        message.success('Workflow created successfully');
      } else {
        savedWorkflow = await updateWorkflow(workflowId!, payload);
        message.success('Workflow updated successfully');
      }

      onSaved?.(savedWorkflow);
      if (isNew) {
        onBack();
      }
    } catch (err: any) {
      if (err.errorFields) {
        message.error('Please fill in all required fields');
      } else {
        message.error(getApiErrorMessage(err, 'Failed to save workflow'));
      }
    } finally {
      setSaving(false);
    }
  };

  const handleExecute = async () => {
    if (!workflowId) return;
    try {
      await executeWorkflow(workflowId);
      message.success('Workflow execution started');
    } catch (err: any) {
      message.error(err.response?.data?.error || 'Failed to execute workflow');
    }
  };

  const addStep = (actionType: string) => {
    const actionInfo = availableActions.find(a => a.action_type === actionType);
    if (!actionInfo) return;

    const newStep: WorkflowStep = {
      order: steps.length,
      name: actionInfo.name,
      action_type: actionType,
      action_config: {},
      timeout_seconds: 300,
      on_failure: 'stop',
      retry_count: 0,
      is_active: true,
    };

    setSteps([...steps, newStep]);
    openStepEditor(steps.length);
  };

  const openStepEditor = (index: number) => {
    const step = steps[index];
    if (!step) return;

    stepForm.setFieldsValue({
      name: step.name,
      action_type: step.action_type,
      action_config: JSON.stringify(step.action_config || {}, null, 2),
      timeout_seconds: step.timeout_seconds,
      on_failure: step.on_failure,
      retry_count: step.retry_count,
      is_active: step.is_active,
    });
    setEditingStepIndex(index);
    setStepModalVisible(true);
  };

  const saveStepEdit = async () => {
    try {
      const values = await stepForm.validateFields();
      if (editingStepIndex === null) return;

      let actionConfig = {};
      try {
        actionConfig = JSON.parse(values.action_config || '{}');
      } catch (e) {
        message.error('Invalid JSON in configuration');
        return;
      }

      const updatedSteps = [...steps];
      updatedSteps[editingStepIndex] = {
        ...updatedSteps[editingStepIndex],
        name: values.name,
        action_config: actionConfig,
        timeout_seconds: values.timeout_seconds,
        on_failure: values.on_failure,
        retry_count: values.retry_count,
        is_active: values.is_active,
      };
      setSteps(updatedSteps);
      setStepModalVisible(false);
      setEditingStepIndex(null);
    } catch (err) {
      // validation error
    }
  };

  const deleteStep = (index: number) => {
    const newSteps = steps.filter((_, i) => i !== index);
    setSteps(newSteps);
  };

  const moveStep = (fromIndex: number, direction: 'up' | 'down') => {
    const toIndex = direction === 'up' ? fromIndex - 1 : fromIndex + 1;
    if (toIndex < 0 || toIndex >= steps.length) return;

    const newSteps = [...steps];
    [newSteps[fromIndex], newSteps[toIndex]] = [newSteps[toIndex], newSteps[fromIndex]];
    setSteps(newSteps);
  };

  const getActionInfo = (actionType: string): ActionInfo | undefined => {
    return availableActions.find(a => a.action_type === actionType);
  };

  const formatConditionPreview = (condition?: Record<string, any>) => {
    if (!condition || Object.keys(condition).length === 0) return '';
    if (condition.field && condition.operator) {
      const value = condition.value ?? condition.compare_to ?? '';
      return `${condition.field} ${condition.operator} ${value}`.trim();
    }
    if (condition.groups && Array.isArray(condition.groups)) {
      return `${condition.groups.length} group(s) (${condition.logic || 'AND'})`;
    }
    return JSON.stringify(condition);
  };

  // Group actions by category
  const actionsByCategory = availableActions.reduce((acc, action) => {
    const cat = action.category || 'utility';
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(action);
    return acc;
  }, {} as Record<string, ActionInfo[]>);

  return (
    <div style={{ padding: 24 }}>
      {/* Header */}
      <Card style={{ marginBottom: 16 }}>
        <Row justify="space-between" align="middle">
          <Col>
            <Space>
              <Button icon={<ArrowLeftOutlined />} onClick={onBack}>
                Back
              </Button>
              <span style={{ fontSize: 18, fontWeight: 600 }}>
                {isNew ? 'Create Workflow' : `Edit: ${workflow?.name || ''}`}
              </span>
              {workflow && (
                <Tag color="blue">v{workflow.version}</Tag>
              )}
              {workflow?.published_version ? (
                <Tooltip title={workflow.published_at ? `Published at ${workflow.published_at}` : 'Published manifest available'}>
                  <Tag color="green">Published v{workflow.published_version}</Tag>
                </Tooltip>
              ) : (
                workflow ? <Tag>Unpublished</Tag> : null
              )}
              {workflow?.has_unpublished_changes ? (
                <Tooltip title="Workflow has been modified since the last publish. Re-publish to push the latest definition to Prefect.">
                  <Tag color="red">Unpublished Changes</Tag>
                </Tooltip>
              ) : null}
              {workflow?.execution_engine === 'local' && (
                <Tag color="red">Legacy Local / unavailable</Tag>
              )}
            </Space>
          </Col>
          <Col>
            <Space>
              {!isNew && (
                <Tooltip
                  title={
                    workflow?.execution_engine === 'local'
                      ? 'Legacy Local execution is unavailable. Publish this workflow to use Prefect.'
                      : !workflow?.is_active
                      ? 'Activate this workflow before execution'
                      : !workflow?.published_version
                        ? 'Publish this workflow before execution'
                        : workflow.has_unpublished_changes
                          ? `Execute the last published version (v${workflow.published_version})`
                          : `Execute published version v${workflow.published_version}`
                  }
                >
                  <span>
                    <Button
                      icon={<PlayCircleOutlined />}
                      onClick={handleExecute}
                      disabled={workflow?.execution_engine === 'local' || !workflow?.is_active || !workflow?.published_version}
                    >
                      Execute
                    </Button>
                  </span>
                </Tooltip>
              )}
              <Button
                icon={<SaveOutlined />}
                onClick={() => handleSave(false)}
                loading={saving}
              >
                Save Draft
              </Button>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                onClick={() => handleSave(true)}
                loading={saving}
              >
                Save & Activate
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Row gutter={16}>
        {/* Workflow Settings */}
        <Col span={8}>
          <Card title="Workflow Settings" loading={loading}>
            <Form form={form} layout="vertical">
              <Form.Item
                name="name"
                label="Workflow Name"
                rules={[{ required: true, message: 'Please enter a name' }]}
              >
                <Input placeholder="My Workflow" />
              </Form.Item>

              <Form.Item name="description" label="Description">
                <TextArea rows={3} placeholder="Describe what this workflow does..." />
              </Form.Item>

              <Form.Item
                name="trigger_type"
                label="Trigger Type"
                rules={[{ required: true }]}
              >
                <Select
                  options={triggerTypes}
                  onChange={(val) => {
                    if (val !== 'webhook') {
                      form.setFieldValue('webhook_source_id', undefined);
                    }
                  }}
                />
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(prev, curr) => prev.trigger_type !== curr.trigger_type || prev.webhook_source_id !== curr.webhook_source_id}>
                {({ getFieldValue }) =>
                  getFieldValue('trigger_type') === 'webhook' && (
                    <Form.Item name="webhook_source_id" label="Webhook Source" rules={[{ required: true, message: 'Please select a webhook source' }]}> 
                      <Select
                        placeholder="Select webhook source"
                        options={webhookInterfaces.map((item) => ({
                          value: item.id,
                          label: item.name,
                        }))}
                      />
                    </Form.Item>
                  )
                }
              </Form.Item>

              <Form.Item
                noStyle
                shouldUpdate={(prev, curr) => prev.trigger_type !== curr.trigger_type}
              >
                {({ getFieldValue }) =>
                  getFieldValue('trigger_type') === 'scheduled' && (
                    <Form.Item name="schedule_cron" label="Cron Schedule">
                      <Input placeholder="0 */4 * * * (every 4 hours)" />
                    </Form.Item>
                  )
                }
              </Form.Item>

              <Form.Item name="tags" label="Tags">
                <Input placeholder="security, incident, alert (comma-separated)" />
              </Form.Item>

              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item name="is_active" label="Active" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="is_draft" label="Draft" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </Col>
              </Row>
            </Form>
          </Card>

          {/* Available Actions */}
          <Card title="Available Actions" style={{ marginTop: 16 }}>
            <Collapse ghost size="small">
              {Object.entries(actionsByCategory).map(([category, actions]) => (
                <Panel
                  header={
                    <Tag color={categoryColors[category] || 'default'}>
                      {category.charAt(0).toUpperCase() + category.slice(1)}
                    </Tag>
                  }
                  key={category}
                >
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {actions.map(action => (
                      <Button
                        key={action.action_type}
                        block
                        size="small"
                        onClick={() => addStep(action.action_type)}
                        style={{ textAlign: 'left' }}
                      >
                        <PlusOutlined /> {action.name}
                      </Button>
                    ))}
                  </Space>
                </Panel>
              ))}
            </Collapse>
          </Card>
        </Col>

        {/* Workflow Steps */}
        <Col span={16}>
          <Card
            title={`Workflow Steps (${steps.length})`}
            loading={loading}
          >
            {steps.length === 0 ? (
              <Empty
                description="No steps yet. Click an action from the left panel to add steps."
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                {steps.map((step, index) => {
                  const actionInfo = getActionInfo(step.action_type);
                  return (
                    <Card
                      key={index}
                      size="small"
                      style={{
                        borderLeft: `3px solid ${categoryColors[actionInfo?.category || 'utility'] ? `var(--ant-${categoryColors[actionInfo?.category || 'utility']})` : '#1890ff'}`,
                        opacity: step.is_active ? 1 : 0.5,
                      }}
                    >
                      <Row justify="space-between" align="middle">
                        <Col>
                          <Space>
                            <Tag>{index + 1}</Tag>
                            <strong>{step.name}</strong>
                            <Tag color={categoryColors[actionInfo?.category || 'utility']}>
                              {step.action_type}
                            </Tag>
                            {!step.is_active && <Tag color="default">Disabled</Tag>}
                          </Space>
                        </Col>
                        <Col>
                          <Space size="small">
                            <Tooltip title="Move Up">
                              <Button
                                size="small"
                                disabled={index === 0}
                                onClick={() => moveStep(index, 'up')}
                              >
                                ↑
                              </Button>
                            </Tooltip>
                            <Tooltip title="Move Down">
                              <Button
                                size="small"
                                disabled={index === steps.length - 1}
                                onClick={() => moveStep(index, 'down')}
                              >
                                ↓
                              </Button>
                            </Tooltip>
                            <Tooltip title="Edit">
                              <Button
                                size="small"
                                icon={<EditOutlined />}
                                onClick={() => openStepEditor(index)}
                              />
                            </Tooltip>
                            <Tooltip title="Delete">
                              <Button
                                size="small"
                                danger
                                icon={<DeleteOutlined />}
                                onClick={() => deleteStep(index)}
                              />
                            </Tooltip>
                          </Space>
                        </Col>
                      </Row>
                      <div style={{ fontSize: 12, color: '#888', marginTop: 8 }}>
                        Timeout: {step.timeout_seconds}s | On Failure: {step.on_failure}
                        {step.retry_count > 0 && ` | Retries: ${step.retry_count}`}
                      </div>
                      {!!step.condition && Object.keys(step.condition).length > 0 && (
                        <div style={{ fontSize: 12, color: '#555', marginTop: 6 }}>
                          Condition: {formatConditionPreview(step.condition)}
                        </div>
                      )}
                    </Card>
                  );
                })}
              </Space>
            )}
          </Card>
        </Col>
      </Row>

      {/* Step Edit Modal */}
      <Modal
        title="Edit Step"
        open={stepModalVisible}
        onOk={saveStepEdit}
        onCancel={() => {
          setStepModalVisible(false);
          setEditingStepIndex(null);
        }}
        width={600}
      >
        {(() => {
          const currentStep = editingStepIndex !== null ? steps[editingStepIndex] : undefined;
          return (
            <Form form={stepForm} layout="vertical">
              <Form.Item
                name="name"
                label="Step Name"
                rules={[{ required: true }]}
              >
                <Input />
              </Form.Item>

              <Form.Item name="action_type" label="Action Type">
                <Input disabled />
              </Form.Item>

              {currentStep?.condition && (
                <Form.Item label="Condition (read-only)">
                  <TextArea
                    rows={4}
                    value={JSON.stringify(currentStep.condition || {}, null, 2)}
                    readOnly
                    style={{ fontFamily: 'monospace' }}
                  />
                </Form.Item>
              )}

              <Form.Item
                name="action_config"
                label="Configuration (JSON)"
                rules={[
                  {
                    validator: (_, value) => {
                      if (!value) return Promise.resolve();
                      try {
                        JSON.parse(value);
                        return Promise.resolve();
                      } catch (e) {
                        return Promise.reject('Invalid JSON');
                      }
                    },
                  },
                ]}
              >
                <TextArea rows={8} style={{ fontFamily: 'monospace' }} />
              </Form.Item>

              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item name="timeout_seconds" label="Timeout (seconds)">
                    <InputNumber min={1} max={3601} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="on_failure" label="On Failure">
                    <Select options={onFailureOptions} />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="retry_count" label="Retry Count">
                    <InputNumber min={0} max={5} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item name="is_active" label="Active" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Form>
          );
        })()}
      </Modal>
    </div>
  );
};

export default WorkflowEditor;

