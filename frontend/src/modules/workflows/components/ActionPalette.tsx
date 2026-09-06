/**
 * Action Palette Component for SOAR Workflow Editor
 */
import React from 'react';
import { Card, Collapse, Tag, Typography, Space, Tooltip, Button } from 'antd';
import {
  DragOutlined,
  SettingOutlined,
  EditOutlined,
  MailOutlined,
  ApiOutlined,
  SearchOutlined,
  LockOutlined,
  UnlockOutlined,
  BranchesOutlined,
  PlayCircleOutlined,
  StopOutlined,
} from '@ant-design/icons';
import type { ActionInfo, SavedWorkflowNode } from 'services/workflows';

const { Panel } = Collapse;
const { Text } = Typography;

const categoryColors: Record<string, string> = {
  utility: '#1890ff',
  notification: '#52c41a',
  integration: '#722ed1',
  enrichment: '#fa8c16',
  containment: '#f5222d',
  release: '#13c2c2',
  control: '#faad14',
};

const hiddenSystemActionCategories = new Set<string>();

const toCategoryLabel = (value: string): string => {
  if (!value) return 'Uncategorized';
  if (value === 'notification') return 'Actions';
  if (value === 'integration') return 'Tickets';
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const actionIcons: Record<string, React.ReactNode> = {
  send_email: <MailOutlined />,
  api_call: <ApiOutlined />,
  ip_lookup: <SearchOutlined />,
  hash_lookup: <SearchOutlined />,
  block_ip: <LockOutlined />,
  disable_user: <LockOutlined />,
  release_ip: <UnlockOutlined />,
  enable_user: <UnlockOutlined />,
  default: <SettingOutlined />,
};

interface DraggableItemProps {
  type: string;
  label: string;
  category?: string;
  description?: string;
  icon?: React.ReactNode;
  template?: SavedWorkflowNode;
}

const DraggableItem: React.FC<DraggableItemProps> = ({
  type,
  label,
  category,
  description,
  icon,
  template,
}) => {
  const onDragStart = (
    event: React.DragEvent,
    nodeType: string,
    nodeLabel: string,
    nodeCategory?: string,
  ) => {
    event.dataTransfer.setData(
      'application/reactflow',
      JSON.stringify({ type: nodeType, label: nodeLabel, category: nodeCategory, template }),
    );
    event.dataTransfer.effectAllowed = 'move';
  };

  const color = categoryColors[category || 'utility'] || '#1890ff';

  return (
    <Tooltip title={description} placement="right">
      <div
        draggable
        onDragStart={(e) => onDragStart(e, type, label, category)}
        style={{
          padding: '8px 12px',
          marginBottom: 8,
          background: '#fff',
          borderRadius: 4,
          border: `1px solid ${color}40`,
          borderLeft: `3px solid ${color}`,
          cursor: 'grab',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          transition: 'all 0.2s',
        }}
      >
        <DragOutlined style={{ color: '#888' }} />
        <span style={{ color, fontSize: 16 }}>{icon || actionIcons.default}</span>
        <Text style={{ flex: 1, fontSize: 13 }}>{label}</Text>
      </div>
    </Tooltip>
  );
};

interface ActionPaletteProps {
  actions: ActionInfo[];
  savedNodes?: SavedWorkflowNode[];
  onManageSavedNodes?: () => void;
}

const ActionPalette: React.FC<ActionPaletteProps> = ({ actions, savedNodes = [], onManageSavedNodes }) => {
  const controlNodes = [
    { type: 'start', label: 'Start', category: 'control', description: 'Workflow entry point', icon: <PlayCircleOutlined /> },
    { type: 'end', label: 'End', category: 'control', description: 'Workflow termination point', icon: <StopOutlined /> },
    { type: 'condition', label: 'Condition', category: 'control', description: 'Branch flow by condition', icon: <BranchesOutlined /> },
  ];

  const actionsByCategory = actions.reduce((acc, action) => {
    const cat = action.category || 'utility';
    if (hiddenSystemActionCategories.has(cat)) return acc;
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(action);
    return acc;
  }, {} as Record<string, ActionInfo[]>);

  const savedNodesByCategory = savedNodes.reduce((acc, node) => {
    const cat = node.node_category || 'utility';
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(node);
    return acc;
  }, {} as Record<string, SavedWorkflowNode[]>);

  const preferredCategoryOrder = ['control', 'enrichment', 'notification', 'integration', 'utility'];
  const dynamicCategories = Array.from(
    new Set([
      ...Object.keys(actionsByCategory),
      ...Object.keys(savedNodesByCategory),
    ])
  ).sort((a, b) => toCategoryLabel(a).localeCompare(toCategoryLabel(b)));
  const categoryOrder = [
    ...preferredCategoryOrder.filter((cat) => cat !== 'control' && dynamicCategories.includes(cat)),
    ...dynamicCategories.filter((cat) => !preferredCategoryOrder.includes(cat)),
  ];

  return (
    <Card
      title="Workflow Nodes"
      size="small"
      extra={
        <Button size="small" icon={<EditOutlined />} onClick={onManageSavedNodes}>
          Edit
        </Button>
      }
      styles={{ body: { padding: 0, maxHeight: 'calc(100vh - 280px)', overflow: 'auto' } }}
    >
      <Collapse defaultActiveKey={['control', 'enrichment']} ghost>
        <Panel
          header={
            <Space>
              <Tag color={categoryColors.control}>Control Flow</Tag>
              <Text type="secondary">({controlNodes.length + (savedNodesByCategory.control?.length || 0)})</Text>
            </Space>
          }
          key="control"
        >
          {controlNodes.map((node) => (
            <DraggableItem key={node.type} type={node.type} label={node.label} category={node.category} description={node.description} icon={node.icon} />
          ))}
          {(savedNodesByCategory.control || []).map((node) => (
            <DraggableItem
              key={node.id}
              type={`saved:${node.id}`}
              label={node.name}
              category={node.node_category}
              description={`${node.node_type} saved node`}
              icon={node.node_type === 'condition' ? <BranchesOutlined /> : actionIcons[node.action_type || ''] || actionIcons.default}
              template={node}
            />
          ))}
        </Panel>

        {categoryOrder
          .filter((cat) => cat !== 'control')
          .map((category) => (
            <Panel
              header={
                <Space>
                  <Tag color={categoryColors[category] || 'default'}>{toCategoryLabel(category)}</Tag>
                  <Text type="secondary">({(actionsByCategory[category]?.length || 0) + (savedNodesByCategory[category]?.length || 0)})</Text>
                </Space>
              }
              key={`action-${category}`}
            >
              {(actionsByCategory[category] || []).map((action) => (
                <DraggableItem
                  key={action.action_type}
                  type={`action:${action.action_type}`}
                  label={action.name}
                  category={category}
                  description={action.description}
                  icon={actionIcons[action.action_type] || actionIcons.default}
                />
              ))}
              {(savedNodesByCategory[category] || []).map((node) => (
                <DraggableItem
                  key={node.id}
                  type={`saved:${node.id}`}
                  label={node.name}
                  category={node.node_category}
                  description={`${node.node_type} saved node`}
                  icon={node.node_type === 'condition' ? <BranchesOutlined /> : actionIcons[node.action_type || ''] || actionIcons.default}
                  template={node}
                />
              ))}
            </Panel>
          ))}
      </Collapse>
    </Card>
  );
};

export default ActionPalette;
