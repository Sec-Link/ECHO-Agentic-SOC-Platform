import React, { useState, useEffect, useCallback } from 'react';
import {
  App,
  Card,
  Table,
  Button,
  Space,
  Tag,
  Input,
  Select,
  Row,
  Col,
  Statistic,
  Modal,
  Popconfirm,
  Tooltip,
  Badge,
} from 'antd';
import {
  PlayCircleOutlined,
  DeleteOutlined,
  CopyOutlined,
  SearchOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  SyncOutlined,
  BranchesOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  listWorkflows,
  deleteWorkflow,
  executeWorkflow,
  cloneWorkflow,
  getWorkflowStats,
  Workflow,
  WorkflowStats,
} from 'services/workflows';

interface WorkflowsProps {
  onNavigate?: (path: string) => void;
  onVisualEditWorkflow?: (id?: string) => void;
}

const triggerTypeLabels: Record<string, string> = {
  manual: 'Manual',
  alert: 'On Alert',
  ticket_created: 'On Ticket Created',
  ticket_status: 'On Ticket Status',
  scheduled: 'Scheduled',
  webhook: 'Webhook',
};

const statusColors: Record<string, string> = {
  completed: 'success',
  running: 'processing',
  pending: 'warning',
  failed: 'error',
  cancelled: 'default',
};

const Workflows: React.FC<WorkflowsProps> = ({ onNavigate, onVisualEditWorkflow }) => {
  const { message } = App.useApp();
  const [modal, modalContextHolder] = Modal.useModal();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<WorkflowStats | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [triggerFilter, setTriggerFilter] = useState<string>('');

  const fetchWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = {};
      if (search) params.search = search;
      if (statusFilter === 'active') params.is_active = true;
      else if (statusFilter === 'inactive') params.is_active = false;
      else if (statusFilter === 'draft') params.is_draft = true;
      if (triggerFilter) params.trigger_type = triggerFilter;

      const data = await listWorkflows(params);
      setWorkflows(Array.isArray(data) ? data : []);
    } catch (err: any) {
      message.error('Failed to load workflows');
    } finally {
      setLoading(false);
    }
  }, [search, statusFilter, triggerFilter]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await getWorkflowStats();
      setStats(data);
    } catch (err) {
      // ignore stats error
    }
  }, []);

  useEffect(() => {
    fetchWorkflows();
    fetchStats();
  }, [fetchWorkflows, fetchStats]);

  const handleDelete = async (id: string) => {
    try {
      await deleteWorkflow(id);
      message.success('Workflow deleted');
      fetchWorkflows();
      fetchStats();
    } catch (err: any) {
      message.error('Failed to delete workflow');
    }
  };

  const handleExecute = async (id: string) => {
    try {
      await executeWorkflow(id);
      message.success('Workflow execution started');
      fetchWorkflows();
    } catch (err: any) {
      const data = err?.response?.data;
      if (data?.requires_confirmation) {
        const estimatedCount = Number(data?.estimated_impact_count || 0);
        const nodeNames = Array.isArray(data?.affected_nodes)
          ? data.affected_nodes.map((item: any) => item?.step_name).filter(Boolean)
          : [];

        modal.confirm({
          title: 'Confirm Update Ticket Execution',
          content: (
            <div>
              <p>Estimated affected tickets: {estimatedCount}</p>
              {nodeNames.length > 0 && <p>Affected nodes: {nodeNames.join(', ')}</p>}
              <p>Do you want to continue?</p>
            </div>
          ),
          okText: 'Confirm & Execute',
          cancelText: 'Cancel',
          onOk: async () => {
            try {
              await executeWorkflow(id, {}, true);
              message.success('Workflow execution started');
              fetchWorkflows();
            } catch (confirmErr: any) {
              message.error(confirmErr?.response?.data?.error || 'Failed to execute workflow');
            }
          },
        });
        return;
      }

      message.error(err.response?.data?.error || 'Failed to execute workflow');
    }
  };

  const handleClone = async (id: string) => {
    try {
      const newWorkflow = await cloneWorkflow(id);
      message.success(`Workflow cloned: ${newWorkflow.name}`);
      fetchWorkflows();
    } catch (err: any) {
      message.error('Failed to clone workflow');
    }
  };

  const columns: ColumnsType<Workflow> = [
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: Workflow) => (
        <Space direction="vertical" size={0}>
          <a onClick={() => onVisualEditWorkflow?.(record.id)} style={{ fontWeight: 500 }}>
            {name}
          </a>
          <span style={{ fontSize: 12, color: '#888' }}>v{record.version}</span>
        </Space>
      ),
    },
    {
      title: 'Trigger',
      dataIndex: 'trigger_type',
      key: 'trigger_type',
      width: 140,
      render: (type: string) => (
        <Tag>{triggerTypeLabels[type] || type}</Tag>
      ),
    },
    {
      title: 'Steps',
      dataIndex: 'step_count',
      key: 'step_count',
      width: 80,
      align: 'center',
      render: (count: number) => count || 0,
    },
    {
      title: 'Status',
      key: 'status',
      width: 120,
      render: (_: any, record: Workflow) => (
        <Space>
          {record.is_draft ? (
            <Tag color="orange">Draft</Tag>
          ) : record.is_active ? (
            <Tag color="green">Active</Tag>
          ) : (
            <Tag color="default">Inactive</Tag>
          )}
        </Space>
      ),
    },
    {
      title: 'Last Execution',
      key: 'last_execution',
      width: 160,
      render: (_: any, record: Workflow) => {
        if (!record.last_execution) {
          return <span style={{ color: '#999' }}>Never</span>;
        }
        const exec = record.last_execution;
        return (
          <Space direction="vertical" size={0}>
            <Badge
              status={statusColors[exec.status] as any || 'default'}
              text={exec.status}
            />
            <span style={{ fontSize: 11, color: '#888' }}>
              {new Date(exec.started_at).toLocaleString()}
            </span>
          </Space>
        );
      },
    },
    {
      title: 'Executions',
      dataIndex: 'execution_count',
      key: 'execution_count',
      width: 100,
      align: 'center',
      render: (count: number) => count || 0,
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 220,
      render: (_: any, record: Workflow) => (
        <Space size="small">
          {record.is_active && !record.is_draft && (
            <Tooltip title="Execute">
              <Button
                type="primary"
                size="small"
                icon={<PlayCircleOutlined />}
                onClick={() => handleExecute(record.id)}
              />
            </Tooltip>
          )}
          <Tooltip title="Visual Editor">
            <Button
              size="small"
              icon={<BranchesOutlined />}
              onClick={() => onVisualEditWorkflow?.(record.id)}
            />
          </Tooltip>
          <Tooltip title="Clone">
            <Button
              size="small"
              icon={<CopyOutlined />}
              onClick={() => handleClone(record.id)}
            />
          </Tooltip>
          <Popconfirm
            title="Delete this workflow?"
            onConfirm={() => handleDelete(record.id)}
            okText="Yes"
            cancelText="No"
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      {modalContextHolder}
      {/* Stats Cards */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Workflows"
              value={stats?.workflows?.total || 0}
              prefix={<BranchesOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Active"
              value={stats?.workflows?.active || 0}
              valueStyle={{ color: '#3f8600' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Executions"
              value={stats?.executions?.total || 0}
              prefix={<SyncOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Success Rate"
              value={stats?.executions?.success_rate || 0}
              precision={1}
              suffix="%"
              valueStyle={{ color: (stats?.executions?.success_rate || 0) >= 80 ? '#3f8600' : '#cf1322' }}
            />
          </Card>
        </Col>
      </Row>

      {/* Filters and Actions */}
      <Card style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Space size="middle">
              <Input
                placeholder="Search workflows..."
                prefix={<SearchOutlined />}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onPressEnter={fetchWorkflows}
                style={{ width: 250 }}
                allowClear
              />
              <Select
                placeholder="Status"
                value={statusFilter || undefined}
                onChange={setStatusFilter}
                style={{ width: 120 }}
                allowClear
              >
                <Select.Option value="active">Active</Select.Option>
                <Select.Option value="inactive">Inactive</Select.Option>
                <Select.Option value="draft">Draft</Select.Option>
              </Select>
              <Select
                placeholder="Trigger Type"
                value={triggerFilter || undefined}
                onChange={setTriggerFilter}
                style={{ width: 150 }}
                allowClear
              >
                {Object.entries(triggerTypeLabels).map(([value, label]) => (
                  <Select.Option key={value} value={value}>{label}</Select.Option>
                ))}
              </Select>
              <Button icon={<ReloadOutlined />} onClick={fetchWorkflows}>
                Refresh
              </Button>
            </Space>
          </Col>
          <Col>
            <Space>
              <Button
                icon={<HistoryOutlined />}
                onClick={() => onNavigate?.('/settings/workflows/executions')}
              >
                View Executions
              </Button>
              <Button
                type="primary"
                icon={<BranchesOutlined />}
                onClick={() => onVisualEditWorkflow?.()}
              >
                Create Visual Workflow
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* Workflows Table */}
      <Card title="Workflows" extra={<span>{workflows.length} items</span>}>
        <Table
          columns={columns}
          dataSource={workflows}
          rowKey="id"
          loading={loading}
          pagination={{ pageSize: 10 }}
        />
      </Card>
    </div>
  );
};

export default Workflows;

