/**
 * Custom ReactFlow Node Components for SOAR Workflow Editor
 *
 * Provides specialised nodes for different workflow step types:
 * - StartNode     : Workflow entry point – shows trigger mode
 * - EndNode       : Workflow termination – logs completion
 * - ConditionNode : Decision / branching node with Yes / No outputs
 * - ActionNode    : Standard action / task node (all other categories)
 * All nodes use a square shape with category-specific colours.
 */
import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Typography, Tooltip } from 'antd';
import {
  PlayCircleOutlined,
  StopOutlined,
  BranchesOutlined,
  SettingOutlined,
  MailOutlined,
  ApiOutlined,
  SearchOutlined,
  LockOutlined,
  UnlockOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

// All nodes are rendered as squares of this size
const NODE_SIZE = 80;

// Colour palette keyed by node type or action category
const nodeColors: Record<string, { bg: string; border: string; icon: string }> = {
  start:        { bg: '#f6ffed', border: '#52c41a', icon: '#52c41a' },
  end:          { bg: '#fff2e8', border: '#f5222d', icon: '#f5222d' },
  condition:    { bg: '#fff7e6', border: '#faad14', icon: '#faad14' },
  control:      { bg: '#fff7e6', border: '#faad14', icon: '#faad14' },
  utility:      { bg: '#e6f7ff', border: '#1890ff', icon: '#1890ff' },
  notification: { bg: '#f6ffed', border: '#52c41a', icon: '#52c41a' },
  integration:  { bg: '#f9f0ff', border: '#722ed1', icon: '#722ed1' },
  enrichment:   { bg: '#fff7e6', border: '#fa8c16', icon: '#fa8c16' },
  containment:  { bg: '#fff1f0', border: '#f5222d', icon: '#f5222d' },
  release:      { bg: '#e6fffb', border: '#13c2c2', icon: '#13c2c2' },
};

// Icon mapping for known action types
const actionIcons: Record<string, React.ReactNode> = {
  send_email:   <MailOutlined />,
  api_call:     <ApiOutlined />,
  ip_lookup:    <SearchOutlined />,
  hash_lookup:  <SearchOutlined />,
  block_ip:     <LockOutlined />,
  disable_user: <LockOutlined />,
  release_ip:   <UnlockOutlined />,
  enable_user:  <UnlockOutlined />,
  default:      <SettingOutlined />,
};

const formatCategory = (category?: string) => {
  if (!category) return 'UTILITY';
  if (category === 'notification') return 'ACTIONS';
  if (category === 'integration') return 'TICKETS';
  return category.replace(/_/g, ' ').toUpperCase();
};

interface CustomNodeData {
  label: string;
  actionType?: string;
  category?: string;
  config?: Record<string, any>;
  condition?: Record<string, any>;
  isActive?: boolean;
  /** Trigger type displayed on the Start node (e.g. 'manual', 'alert') */
  triggerType?: string;
  onEdit?: () => void;
  onDelete?: () => void;
}

// Shared square style for all node types
const getSquareNodeStyle = (
  colorKey: string,
  selected: boolean,
  isActive = true,
): React.CSSProperties => {
  const colors = nodeColors[colorKey] || nodeColors.utility;
  return {
    width: NODE_SIZE,
    height: NODE_SIZE,
    background: selected ? `${colors.border}15` : colors.bg,
    border: `2px solid ${selected ? '#1890ff' : colors.border}`,
    borderRadius: 8,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    boxShadow: selected ? '0 0 0 2px #1890ff' : '0 2px 8px rgba(0,0,0,0.1)',
    opacity: isActive ? 1 : 0.5,
    transition: 'all 0.2s',
  };
};

// Human-readable labels for trigger types shown on the Start node
const triggerLabels: Record<string, string> = {
  manual:         'Manual',
  alert:          'On Alert',
  ticket_created: 'On Ticket',
  ticket_status:  'On Status',
  scheduled:      'Scheduled',
  webhook:        'Webhook',
};

// ── Start Node ─────────────────────────────────────────────────────────────
// Entry point of the workflow.  Displays the configured trigger mode so
// users can immediately see how the playbook is launched.
export const StartNode = memo(({ data, selected }: NodeProps<CustomNodeData>) => {
  const colors = nodeColors.start;
  const triggerLabel = data?.triggerType
    ? (triggerLabels[data.triggerType] || data.triggerType)
    : '';

  return (
    <div style={getSquareNodeStyle('start', selected)}>
      <PlayCircleOutlined style={{ fontSize: 24, color: colors.icon }} />
      <Text strong style={{ fontSize: 11, marginTop: 2, color: colors.icon }}>
        {data?.label || 'Start'}
      </Text>
      {triggerLabel && (
        <Text style={{ fontSize: 9, color: colors.icon, opacity: 0.75 }}>
          {triggerLabel}
        </Text>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: colors.border, width: 10, height: 10 }}
      />
    </div>
  );
});

// ── End Node ───────────────────────────────────────────────────────────────
// Termination point of the workflow.  The execution engine logs completion
// when this node is reached.
export const EndNode = memo(({ data, selected }: NodeProps<CustomNodeData>) => {
  const colors = nodeColors.end;
  return (
    <div style={getSquareNodeStyle('end', selected)}>
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: colors.border, width: 10, height: 10 }}
      />
      <StopOutlined style={{ fontSize: 24, color: colors.icon }} />
      <Text strong style={{ fontSize: 11, marginTop: 2, color: colors.icon }}>
        {data?.label || 'End'}
      </Text>
      <Text style={{ fontSize: 9, color: colors.icon, opacity: 0.75 }}>
        Log & finish
      </Text>
    </div>
  );
});

// ── Action Node ────────────────────────────────────────────────────────────
// Standard workflow action – colour and icon reflect its category.
export const ActionNode = memo(({ data, selected }: NodeProps<CustomNodeData>) => {
  const colorKey = data.category || 'utility';
  const colors = nodeColors[colorKey] || nodeColors.utility;
  const icon = actionIcons[data.actionType || ''] || actionIcons.default;

  return (
    <div style={getSquareNodeStyle(colorKey, selected, data.isActive !== false)}>
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: colors.border, width: 8, height: 8 }}
      />
      <div style={{ fontSize: 24, color: colors.icon }}>{icon}</div>
      <Tooltip title={data.actionType}>
        <Text
          strong
          ellipsis
          style={{
            fontSize: 10,
            marginTop: 4,
            maxWidth: NODE_SIZE - 10,
            textAlign: 'center',
            color: colors.icon,
          }}
        >
          {data.label}
        </Text>
      </Tooltip>
      <Text style={{ fontSize: 8, color: colors.icon, opacity: 0.7 }}>
        {formatCategory(data.category)}
      </Text>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: colors.border, width: 8, height: 8 }}
      />
    </div>
  );
});

// ── Condition Node ─────────────────────────────────────────────────────────
// Branching node with two outputs:
//   Right handle (green)  → condition is TRUE  (Yes)
//   Bottom handle (red)   → condition is FALSE (No)
export const ConditionNode = memo(({ data, selected }: NodeProps<CustomNodeData>) => {
  const colors = nodeColors.condition;
  const conditionDisplay = data.condition
    ? JSON.stringify(data.condition).slice(0, 30) +
      (JSON.stringify(data.condition).length > 30 ? '…' : '')
    : 'Condition';

  return (
    <div style={{ position: 'relative' }}>
      <div style={getSquareNodeStyle('condition', selected)}>
        <Handle
          type="target"
          position={Position.Top}
          style={{ background: colors.border, width: 8, height: 8 }}
        />
        <BranchesOutlined style={{ fontSize: 24, color: colors.icon }} />
        <Tooltip title={conditionDisplay}>
          <Text
            strong
            ellipsis
            style={{
              fontSize: 10,
              marginTop: 4,
              maxWidth: NODE_SIZE - 10,
              textAlign: 'center',
              color: colors.icon,
            }}
          >
            {data.label || 'Condition'}
          </Text>
        </Tooltip>
        <Text style={{ fontSize: 8, color: colors.icon, opacity: 0.7 }}>
          {formatCategory(data.category || 'control')}
        </Text>
        {/* TRUE branch → right */}
        <Handle
          type="source"
          position={Position.Right}
          id="true"
          style={{ background: '#52c41a', width: 8, height: 8 }}
        />
        {/* FALSE branch → bottom */}
        <Handle
          type="source"
          position={Position.Bottom}
          id="false"
          style={{ background: '#f5222d', width: 8, height: 8 }}
        />
      </div>
      {/* Branch labels */}
      <div
        style={{
          position: 'absolute',
          right: -28,
          top: '50%',
          transform: 'translateY(-50%)',
          fontSize: 9,
          color: '#52c41a',
          fontWeight: 'bold',
        }}
      >
        Yes
      </div>
      <div
        style={{
          position: 'absolute',
          bottom: -16,
          left: '50%',
          transform: 'translateX(-50%)',
          fontSize: 9,
          color: '#f5222d',
          fontWeight: 'bold',
        }}
      >
        No
      </div>
    </div>
  );
});

// Node type registry for ReactFlow
// Note: 'parallel' has been intentionally removed – use multiple Condition
// nodes or direct connections for parallel-like flows.
export const nodeTypes = {
  start:     StartNode,
  end:       EndNode,
  action:    ActionNode,
  condition: ConditionNode,
};
