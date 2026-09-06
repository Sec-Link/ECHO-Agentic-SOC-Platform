/**
 * Visual Workflow Editor Component for SOAR Playbook
 *
 * A drag-and-drop workflow editor using ReactFlow that allows users to:
 * - Visually design playbooks by dragging nodes from the palette
 * - Create connections between nodes
 * - Configure condition nodes for branching logic
 * - Edit action configurations
 */
import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  Background,
  MiniMap,
  Connection,
  addEdge,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
  ReactFlowInstance,
  MarkerType,
  BackgroundVariant,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
  Card,
  Form,
  Input,
  Select,
  AutoComplete,
  Button,
  Space,
  Row,
  Col,
  Switch,
  message,
  Modal,
  InputNumber,
  Drawer,
  Divider,
  Tag,
  Typography,
  List,
  Popconfirm,
  Empty,
  Tooltip,
} from 'antd';
import {
  SaveOutlined,
  ArrowLeftOutlined,
  PlayCircleOutlined,
  CheckOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SettingOutlined,
  BranchesOutlined,
  TagsOutlined,
} from '@ant-design/icons';
import {
  getWorkflow,
  createWorkflow,
  updateWorkflow,
  getAvailableActions,
  executeWorkflow,
  listSavedWorkflowNodes,
  createSavedWorkflowNode,
  updateSavedWorkflowNode,
  deleteSavedWorkflowNode,
  listTicketWorkflowBindings,
  createTicketWorkflowBinding,
  updateTicketWorkflowBinding,
  deleteTicketWorkflowBinding,
} from 'services/workflows';
import { fetchSlaTicketFieldChoices } from 'services/tickets';
import { listInterfaceEndpoints } from 'services/interfaces';
import type {
  Workflow,
  WorkflowStep,
  ActionInfo,
  WorkflowEdge,
  SavedWorkflowNode,
  TicketWorkflowBinding,
} from 'services/workflows';
import type { InterfaceEndpoint } from 'services/interfaces';
import { nodeTypes } from './CustomNodes';
import ActionPalette from './ActionPalette';
import ConditionBuilder from './ConditionBuilder';
import ActionConfigBuilder from './ActionConfigBuilder';

const { Text } = Typography;

interface VisualWorkflowEditorProps {
  workflowId?: string;
  onBack: () => void;
  onSaved?: (workflow: Workflow) => void;
}

interface TriggerRule {
  field: string;
  operator: '==' | '!=' | 'contains';
  value: string;
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

const triggerOperatorOptions = [
  { value: '==', label: 'Equals' },
  { value: '!=', label: 'Not Equals' },
  { value: 'contains', label: 'Contains' },
];

const triggerLogicOptions = [
  { value: 'AND', label: 'AND (all conditions match)' },
  { value: 'OR', label: 'OR (any condition matches)' },
];

const alertTriggerFieldOptions = [
  { value: 'alert_id', label: 'Alert ID' },
  { value: 'severity', label: 'Severity' },
  { value: 'rule_id', label: 'Rule ID' },
  { value: 'title', label: 'Title' },
  { value: 'category', label: 'Category' },
  { value: 'source_index', label: 'Source Index' },
  { value: 'message', label: 'Message' },
];

const hiddenSystemActionCategories = new Set<string>();

const defaultNodeCategoryOptions = [
  { value: 'notification', label: 'Actions' },
  { value: 'enrichment', label: 'Enrichment' },
  { value: 'integration', label: 'Tickets' },
  { value: 'utility', label: 'Utility' },
  { value: 'control', label: 'Control' },
];

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

const normalizeCategoryValue = (value: unknown, fallback = 'utility'): string => {
  if (typeof value === 'string') {
    const normalized = value.trim();
    return normalized || fallback;
  }
  return fallback;
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

const normalizeBindingLabelFilters = (value: any): Array<{ label_name: string; label_value: string }> => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => ({
      label_name: String(item?.label_name || '').trim(),
      label_value: item?.label_value == null ? '' : String(item.label_value).trim(),
    }))
    .filter((item) => item.label_name);
};

const normalizeTriggerRules = (value: any): TriggerRule[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => ({
      field: typeof item?.field === 'string' ? item.field.trim() : '',
      operator: item?.operator === '!=' || item?.operator === 'contains' ? item.operator : '==',
      value: typeof item?.value === 'string' ? item.value.trim() : String(item?.value || '').trim(),
    }))
    .filter((item) => item.field && item.value);
};

// Generate unique node ID

const generateNodeId = () => {
  const cryptoObj = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (cryptoObj && 'randomUUID' in cryptoObj && typeof cryptoObj.randomUUID === 'function') {
    return cryptoObj.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (cryptoObj && 'getRandomValues' in cryptoObj && typeof cryptoObj.getRandomValues === 'function') {
    cryptoObj.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
};

// Convert workflow steps to ReactFlow nodes
const stepsToNodes = (steps: WorkflowStep[]): Node[] => {
  return steps.map((step, index) => ({
    id: String(step.id || generateNodeId()),
    type: step.node_type || 'action',
    position: { x: step.position_x || 250, y: step.position_y || index * 150 + 50 },
    data: {
      label: step.name,
      actionType: step.action_type,
      category: step.node_category || 'utility',
      config: step.action_config,
      configuredSecretFields: step.configured_secret_fields || [],
      condition: step.condition,
      isActive: step.is_active,
    },
  }));
};

const stepsToEdgesFromConnections = (steps: WorkflowStep[]): Edge[] => {
  const edges: Edge[] = [];
  const stepIds = new Set(steps.map((step) => String(step.id)));
  const pushEdge = (source: string, target: string, sourceHandle?: string, label?: string) => {
    if (!source || !target) return;
    if (!stepIds.has(source) || !stepIds.has(target)) return;
    edges.push({
      id: `edge_${source}_${target}_${edges.length}`,
      source,
      target,
      sourceHandle,
      type: 'smoothstep',
      markerEnd: { type: MarkerType.ArrowClosed },
      style: { strokeWidth: 2 },
      label,
      labelStyle: label ? { fill: label === 'Yes' ? '#52c41a' : label === 'No' ? '#f5222d' : undefined } : undefined,
    });
  };

  steps.forEach((step) => {
    const source = String(step.id);
    if (step.node_type === 'condition') {
      if (step.next_step_true) {
        pushEdge(source, String(step.next_step_true), 'true', 'Yes');
      }
      if (step.next_step_false) {
        pushEdge(source, String(step.next_step_false), 'false', 'No');
      }
    }

    if (Array.isArray(step.connections)) {
      step.connections.forEach((target) => pushEdge(source, String(target)));
    }
  });

  return edges;
};

// Convert ReactFlow nodes back to workflow steps
const nodesToSteps = (nodes: Node[], edges: Edge[]): WorkflowStep[] => {
  return nodes
    .filter(node => node.type !== 'start')
    .map((node, index) => {
      // Find outgoing edges for condition nodes
      const trueEdge = edges.find(e => e.source === node.id && e.sourceHandle === 'true');
      const falseEdge = edges.find(e => e.source === node.id && e.sourceHandle === 'false');
      const connections = edges
        .filter(e => e.source === node.id)
        .map(e => e.target);

      const isActionNode = node.type === 'action' || (node.type as string).startsWith('action:');
      const actionType = isActionNode
        ? (node.data.actionType || (node.type as string).replace('action:', ''))
        : (node.type as string);

      return {
        id: node.id,
        order: index,
        name: node.data.label || 'Unnamed Step',
        node_type: node.type === 'action' || (node.type as string).startsWith('action:') ? 'action' : node.type,
        node_category: node.data.category || 'utility',
        position_x: node.position.x,
        position_y: node.position.y,
        action_type: actionType,
        action_config: node.data.config || {},
        timeout_seconds: node.data.timeout || 300,
        on_failure: node.data.onFailure || 'stop',
        retry_count: node.data.retryCount || 0,
        retry_delay_seconds: node.data.retryDelaySeconds || 30,
        condition: node.data.condition || {},
        next_step_true: trueEdge?.target,
        next_step_false: falseEdge?.target,
        connections,
        is_active: node.data.isActive !== false,
      } as WorkflowStep;
    });
};

// Convert workflow edges to ReactFlow edges
const workflowEdgesToFlowEdges = (edges: WorkflowEdge[]): Edge[] => {
  return edges.map(edge => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
    label: edge.label,
    type: 'smoothstep',
    markerEnd: { type: MarkerType.ArrowClosed },
    style: { strokeWidth: 2 },
  }));
};

// Convert ReactFlow edges back to workflow edges
const flowEdgesToWorkflowEdges = (edges: Edge[]): WorkflowEdge[] => {
  return edges.map(edge => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle || undefined,
    targetHandle: edge.targetHandle || undefined,
    label: typeof edge.label === 'string' ? edge.label : undefined,
  }));
};

const VisualWorkflowEditor: React.FC<VisualWorkflowEditorProps> = ({
  workflowId,
  onBack,
  onSaved,
}) => {
  const [modal, modalContextHolder] = Modal.useModal();
  const [form] = Form.useForm();
  const [bindingForm] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [availableActions, setAvailableActions] = useState<ActionInfo[]>([]);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [nodeDrawerVisible, setNodeDrawerVisible] = useState(false);
  const [conditionModalVisible, setConditionModalVisible] = useState(false);
  const [actionConfig, setActionConfig] = useState<Record<string, any>>({});
  const [savedNodes, setSavedNodes] = useState<SavedWorkflowNode[]>([]);
  const [ticketFieldChoices, setTicketFieldChoices] = useState<Record<string, Array<{ value: string; label: string }>>>({});
  const [savedNodesManagerVisible, setSavedNodesManagerVisible] = useState(false);
  const [savedNodeFormVisible, setSavedNodeFormVisible] = useState(false);
  const [editingSavedNode, setEditingSavedNode] = useState<SavedWorkflowNode | null>(null);
  const [saveAsConfirmVisible, setSaveAsConfirmVisible] = useState(false);
  const [webhookInterfaces, setWebhookInterfaces] = useState<InterfaceEndpoint[]>([]);
  const [nodeForm] = Form.useForm();
  const [savedNodeForm] = Form.useForm();
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null);
  const [deleteConfirmVisible, setDeleteConfirmVisible] = useState(false);
  const [ticketBinding, setTicketBinding] = useState<TicketWorkflowBinding | null>(null);
  const [bindingLoading, setBindingLoading] = useState(false);
  const [bindingSaving, setBindingSaving] = useState(false);

  const isNew = !workflowId;

  const onNodesDelete = useCallback(
    (deleted: Node[]) => {
      if (!deleted.length) return;
      const deletedIds = new Set(deleted.map((node) => String(node.id)));
      setEdges((eds) =>
        eds.filter((e) => !deletedIds.has(String(e.source)) && !deletedIds.has(String(e.target)))
      );
      if (selectedNode && deletedIds.has(String(selectedNode.id))) {
        setNodeDrawerVisible(false);
        setSelectedNode(null);
      }
    },
    [selectedNode, setEdges]
  );

  // Hook fired by ReactFlow after edges are removed (via Delete/Backspace key,
  // programmatic onEdgesChange, or our onEdgeDoubleClick handler). Kept as a
  // no-op placeholder so we own the lifecycle and can plug in side effects
  // (e.g. analytics, persisted-state cleanup) in the future without rewiring.
  const onEdgesDelete = useCallback((_deleted: Edge[]) => {
    // intentional no-op: useEdgesState already drops the edges from state
  }, []);

  // Double-click an edge to delete it. This is the primary, focus-independent
  // delete affordance — keyboard delete only fires when ReactFlow has focus,
  // which fails as soon as the user is typing in the left-hand config form.
  const onEdgeDoubleClick = useCallback(
    (_event: React.MouseEvent, edge: Edge) => {
      setEdges((eds) => eds.filter((e) => e.id !== edge.id));
    },
    [setEdges]
  );

  const getCurrentNodeById = useCallback(
    (nodeId: string) => nodes.find((node) => String(node.id) === nodeId),
    [nodes]
  );

  const refreshSavedNodes = useCallback(async () => {
    try {
      const data = await listSavedWorkflowNodes();
      setSavedNodes(Array.isArray(data) ? data : []);
    } catch {
      setSavedNodes([]);
    }
  }, []);

  const refreshTicketBinding = useCallback(async () => {
    if (!workflowId) {
      setTicketBinding(null);
      bindingForm.resetFields();
      return;
    }

    setBindingLoading(true);
    try {
      const data = await listTicketWorkflowBindings({ workflow: workflowId });
      const existing = Array.isArray(data) ? data[0] || null : null;
      setTicketBinding(existing);
      bindingForm.setFieldsValue({
        name: existing?.name || `${workflow?.name || 'Workflow'} ticket binding`,
        label_filter_logic: existing?.label_filter_logic || 'AND',
        label_filters: existing?.label_filters?.length ? existing.label_filters : [{ label_name: '', label_value: '' }],
      });
    } catch {
      setTicketBinding(null);
      bindingForm.setFieldsValue({
        name: `${workflow?.name || 'Workflow'} ticket binding`,
        label_filter_logic: 'AND',
        label_filters: [{ label_name: '', label_value: '' }],
      });
    } finally {
      setBindingLoading(false);
    }
  }, [workflowId, workflow?.name, bindingForm]);

  const handleTriggerTypeChange = useCallback(
    (triggerType: string) => {
      if (triggerType !== 'webhook') {
        form.setFieldValue('webhook_source_id', undefined);
      }
      if (triggerType !== 'alert') {
        form.setFieldValue('alert_filters', []);
        form.setFieldValue('alert_filter_logic', 'AND');
      }
      if (triggerType !== 'ticket_created') {
        form.setFieldValue('ticket_filters', []);
        form.setFieldValue('ticket_filter_logic', 'AND');
      }
      if (triggerType !== 'scheduled') {
        form.setFieldValue('schedule_cron', undefined);
      }

      setNodes((nds) =>
        nds.map((n) =>
          n.type === 'start' || n.id === 'start'
            ? { ...n, data: { ...n.data, triggerType } }
            : n,
        ),
      );
    },
    [form, setNodes]
  );

  const buildTriggerConditions = useCallback((values: any) => {
    const triggerType = values.trigger_type;
    if (triggerType === 'webhook') {
      return values.webhook_source_id ? { webhook_source_id: values.webhook_source_id } : {};
    }
    if (triggerType === 'alert') {
      const alertFilters = normalizeTriggerRules(values.alert_filters);
      return alertFilters.length
        ? {
            alert_filters: alertFilters,
            alert_filter_logic: values.alert_filter_logic === 'OR' ? 'OR' : 'AND',
          }
        : {};
    }
    if (triggerType === 'ticket_created') {
      const ticketFilters = normalizeTriggerRules(values.ticket_filters).map((rule) => {
        if (rule.field !== 'labels') return rule;
        return {
          ...rule,
          operator: 'contains' as const,
          value: String(rule.value || '').trim(),
        };
      });
      const payload: Record<string, any> = {};
      if (ticketFilters.length) payload.ticket_filters = ticketFilters;
      if (ticketFilters.length) payload.ticket_filter_logic = values.ticket_filter_logic === 'OR' ? 'OR' : 'AND';
      return payload;
    }
    return {};
  }, []);

  const categoryOptions = useMemo(() => {
    const options = new Map<string, { value: string; label: string }>();
    defaultNodeCategoryOptions.forEach((item) => {
      options.set(item.value, item);
    });

    availableActions.forEach((action) => {
      const key = normalizeCategoryValue(action.category, 'utility');
      if (hiddenSystemActionCategories.has(key)) {
        return;
      }
      if (!options.has(key)) {
        options.set(key, { value: key, label: toCategoryLabel(key) });
      }
    });

    savedNodes.forEach((node) => {
      const key = normalizeCategoryValue(node.node_category, 'utility');
      if (!options.has(key)) {
        options.set(key, { value: key, label: toCategoryLabel(key) });
      }
    });

    nodes.forEach((node) => {
      const key = normalizeCategoryValue(node.data?.category, node.type === 'condition' ? 'control' : 'utility');
      if (!options.has(key)) {
        options.set(key, { value: key, label: toCategoryLabel(key) });
      }
    });

    return Array.from(options.values()).sort((a, b) => a.label.localeCompare(b.label));
  }, [availableActions, savedNodes, nodes]);

  const ticketTriggerFieldOptions = useMemo(() => {
    const options: Array<{ value: string; label: string }> = [
      { value: 'ticket_number', label: 'Ticket Number' },
      { value: 'title', label: 'Title' },
      { value: 'create_uid', label: 'Creator' },
    ];

    const statusChoices = Array.isArray(ticketFieldChoices.status_choices) ? ticketFieldChoices.status_choices : [];
    const priorityChoices = Array.isArray(ticketFieldChoices.priority_choices) ? ticketFieldChoices.priority_choices : [];
    const eventCategoryChoices = Array.isArray(ticketFieldChoices.event_category_choices) ? ticketFieldChoices.event_category_choices : [];
    const eventResultChoices = Array.isArray(ticketFieldChoices.event_result_choices) ? ticketFieldChoices.event_result_choices : [];

    if (statusChoices.length) options.push({ value: 'status', label: 'Status' });
    if (priorityChoices.length) options.push({ value: 'priority', label: 'Priority' });
    if (eventCategoryChoices.length) options.push({ value: 'event_category', label: 'Event Category' });
    if (eventResultChoices.length) options.push({ value: 'event_result', label: 'Event Result' });
    options.push({ value: 'labels', label: 'Label (name:value)' });

    return options;
  }, [ticketFieldChoices]);

  const ticketFieldValueOptions = useMemo(() => {
    return {
      status: Array.isArray(ticketFieldChoices.status_choices) ? ticketFieldChoices.status_choices : [],
      priority: Array.isArray(ticketFieldChoices.priority_choices) ? ticketFieldChoices.priority_choices : [],
      event_category: Array.isArray(ticketFieldChoices.event_category_choices) ? ticketFieldChoices.event_category_choices : [],
      event_result: Array.isArray(ticketFieldChoices.event_result_choices) ? ticketFieldChoices.event_result_choices : [],
    } as Record<string, Array<{ value: string; label: string }>>;
  }, [ticketFieldChoices]);

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
    refreshSavedNodes();
  }, [refreshSavedNodes]);

  useEffect(() => {
    refreshTicketBinding();
  }, [refreshTicketBinding]);

  useEffect(() => {
    const loadInterfaces = async () => {
      try {
        const data = await listInterfaceEndpoints({ interface_type: 'webhook', is_active: true });
        setWebhookInterfaces(Array.isArray(data) ? data : []);
      } catch {
        setWebhookInterfaces([]);
      }
    };
    loadInterfaces();
  }, []);

  useEffect(() => {
    const loadTicketFieldChoices = async () => {
      try {
        const data = await fetchSlaTicketFieldChoices();
        setTicketFieldChoices(data || {});
      } catch {
        setTicketFieldChoices({});
      }
    };
    loadTicketFieldChoices();
  }, []);

  // Load workflow if editing
  useEffect(() => {
    if (!workflowId) {
      form.setFieldsValue({
        name: '',
        description: '',
        trigger_type: 'manual',
        webhook_source_id: undefined,
        alert_filters: [],
        alert_filter_logic: 'AND',
        ticket_filters: [],
        ticket_filter_logic: 'AND',
        is_active: false,
        is_draft: true,
        tags: [],
      });
      // Add default start node
      setNodes([
        {
          id: 'start',
          type: 'start',
          position: { x: 250, y: 50 },
          data: { label: 'Start' },
        },
      ]);
      setEdges([]);
      return;
    }

    const loadWorkflow = async () => {
      try {
        const data = await getWorkflow(workflowId);
        setWorkflow(data);
        form.setFieldsValue({
          name: data.name,
          description: data.description,
          trigger_type: data.trigger_type,
          webhook_source_id: data.trigger_conditions?.webhook_source_id,
          alert_filters: Array.isArray(data.trigger_conditions?.alert_filters) ? data.trigger_conditions.alert_filters : [],
          alert_filter_logic: data.trigger_conditions?.alert_filter_logic === 'OR' ? 'OR' : 'AND',
          ticket_filters: Array.isArray(data.trigger_conditions?.ticket_filters) ? data.trigger_conditions.ticket_filters : [],
          ticket_filter_logic: data.trigger_conditions?.ticket_filter_logic === 'OR' ? 'OR' : 'AND',
          schedule_cron: data.schedule_cron,
          is_active: data.is_active,
          is_draft: data.is_draft,
          tags: data.tags?.join(', ') || '',
        });

        // Convert steps to nodes directly using backend IDs
        const rawSteps = data.steps || [];
        const stepNodes = stepsToNodes(rawSteps);

        // Build set of all step IDs
        const stepIdSet = new Set(stepNodes.map((node) => node.id));

        // Check edges for referenced nodes that don't exist as steps
        const savedEdges = data.edges || [];
        savedEdges.forEach((edge) => {
          if (edge.source) stepIdSet.add(String(edge.source));
          if (edge.target) stepIdSet.add(String(edge.target));
        });

        // Find IDs referenced in edges but not in steps (missing nodes)
        const allNodes: Node[] = [...stepNodes];
        const maxY = stepNodes.length > 0
          ? stepNodes.reduce((max, node) => Math.max(max, node.position.y), 0)
          : 0;

        savedEdges.forEach((edge) => {
          // Check if target node is missing (could be an end node)
          const targetId = String(edge.target);
          if (targetId && targetId !== 'start' && !allNodes.some((n) => n.id === targetId)) {
            allNodes.push({
              id: targetId,
              type: 'end',
              position: { x: 250, y: maxY + 150 },
              data: { label: 'End' },
            });
          }
        });

        // Add start node if not present
        const hasStart = allNodes.some((n) => n.type === 'start' || n.id === 'start');
        if (!hasStart) {
          allNodes.unshift({
            id: 'start',
            type: 'start',
            position: { x: 250, y: 0 },
            data: { label: 'Start', triggerType: data.trigger_type },
          });
        } else {
          // Update the existing start node with the workflow trigger type
          const startIdx = allNodes.findIndex((n) => n.type === 'start' || n.id === 'start');
          if (startIdx >= 0) {
            allNodes[startIdx] = {
              ...allNodes[startIdx],
              data: { ...allNodes[startIdx].data, triggerType: data.trigger_type },
            };
          }
        }

        setNodes(allNodes);

        const nodeIds = new Set(allNodes.map((node) => node.id));

        // Convert saved edges to ReactFlow edges
        if (savedEdges.length > 0) {
          const flowEdges = workflowEdgesToFlowEdges(savedEdges);
          const validEdges = flowEdges.filter(
            (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)
          );
          setEdges(validEdges);
        } else {
          // Rebuild edges from step connections if no saved edges
          const rebuiltEdges = stepsToEdgesFromConnections(rawSteps);
          setEdges(rebuiltEdges);
        }
      } catch (err) {
        message.error('Failed to load workflow');
      }
    };
    loadWorkflow();
  }, [workflowId, form]);

  // Handle connecting nodes
  const onConnect = useCallback(
    (params: Connection) => {
      const newEdge: Edge = {
        ...params,
        id: `edge_${params.source}_${params.target}_${Date.now()}`,
        type: 'smoothstep',
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { strokeWidth: 2 },
        // Add labels for condition branches
        label: params.sourceHandle === 'true' ? 'Yes' : params.sourceHandle === 'false' ? 'No' : undefined,
        labelStyle: { fill: params.sourceHandle === 'true' ? '#52c41a' : params.sourceHandle === 'false' ? '#f5222d' : undefined },
      } as Edge;
      setEdges((eds) => addEdge(newEdge, eds));
    },
    [setEdges]
  );

  // Handle dropping new nodes
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      if (!reactFlowWrapper.current || !reactFlowInstance) return;

      const reactFlowBounds = reactFlowWrapper.current.getBoundingClientRect();
      const data = JSON.parse(event.dataTransfer.getData('application/reactflow'));

      const position = reactFlowInstance.project({
        x: event.clientX - reactFlowBounds.left,
        y: event.clientY - reactFlowBounds.top,
      });

      let nodeType = data.type;
      let nodeData: any = {
        label: data.label,
        category: data.category,
        isActive: true,
      };

      if (typeof data.type === 'string' && data.type.startsWith('saved:') && data.template) {
        const template = data.template as SavedWorkflowNode;
        const mappedType = template.node_type === 'action' ? 'action' : template.node_type;
        const newNode: Node = {
          id: generateNodeId(),
          type: mappedType,
          position,
          data: {
            label: template.name,
            category: template.node_category,
            actionType: template.action_type,
            config: template.action_config || {},
            configuredSecretFields: template.configured_secret_fields || [],
            condition: template.condition || {},
            timeout: template.timeout_seconds,
            onFailure: template.on_failure,
            retryCount: template.retry_count,
            retryDelaySeconds: template.retry_delay_seconds,
            isActive: template.is_active,
          },
        };
        setNodes((nds) => nds.concat(newNode));
        return;
      }

      // Handle action nodes
      if (data.type.startsWith('action:')) {
        const actionType = data.type.replace('action:', '');
        nodeType = 'action';
        nodeData = {
          ...nodeData,
          actionType,
          config: {},
        };
      } else if (data.type === 'condition') {
        nodeData = {
          ...nodeData,
          condition: {},
        };
      }

      const newNode: Node = {
        id: generateNodeId(),
        type: nodeType,
        position,
        data: nodeData,
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [reactFlowInstance, setNodes]
  );

  // Handle node click (open configuration)
  const onNodeClick = useCallback((event: React.MouseEvent, node: Node) => {
    setSelectedNode(node);
    setActionConfig(node.data.config || {});

    // Set form values based on node type
    if (node.type === 'start' || node.type === 'end') {
      nodeForm.setFieldsValue({
        name: node.data.label || (node.type === 'start' ? 'Start' : 'End'),
        node_category: node.data.category || 'control',
        is_active: true,
      });
    } else if (node.type === 'condition') {
      nodeForm.setFieldsValue({
        name: node.data.label || 'Condition',
        node_category: node.data.category || 'control',
        timeout_seconds: node.data.timeout || 300,
        on_failure: node.data.onFailure || 'stop',
        retry_count: node.data.retryCount || 0,
        retry_delay_seconds: node.data.retryDelaySeconds || 30,
        is_active: node.data.isActive !== false,
      });
    } else {
      nodeForm.setFieldsValue({
        name: node.data.label,
        node_category: node.data.category || 'utility',
        timeout_seconds: node.data.timeout || 300,
        on_failure: node.data.onFailure || 'stop',
        retry_count: node.data.retryCount || 0,
        retry_delay_seconds: node.data.retryDelaySeconds || 30,
        is_active: node.data.isActive !== false,
      });
    }
    setNodeDrawerVisible(true);
  }, [nodeForm]);

  // Handle node configuration save
  const saveNodeConfig = async () => {
    if (!selectedNode) return;

    try {
      const values = await nodeForm.validateFields();

      // Handle Start/End nodes - only update label
      if (selectedNode.type === 'start' || selectedNode.type === 'end') {
        setNodes((nds) =>
          nds.map((node) => {
            if (node.id === selectedNode.id) {
              return {
                ...node,
                data: {
                  ...node.data,
                  label: values.name,
                },
              };
            }
            return node;
          })
        );
        setNodeDrawerVisible(false);
        setSelectedNode(null);
        message.success('Node updated');
        return;
      }

      // Handle action and other nodes - use actionConfig state
      setNodes((nds) =>
        nds.map((node) => {
          if (node.id === selectedNode.id) {
            const normalizedCategory = normalizeCategoryValue(
              values.node_category,
              node.type === 'condition' ? 'control' : node.data.category || 'utility'
            );
            return {
              ...node,
              data: {
                ...node.data,
                label: values.name,
                category: normalizedCategory,
                config: actionConfig,
                timeout: values.timeout_seconds,
                onFailure: values.on_failure,
                retryCount: values.retry_count,
                retryDelaySeconds: values.retry_delay_seconds,
                isActive: values.is_active,
              },
            };
          }
          return node;
        })
      );

      setNodeDrawerVisible(false);
      setSelectedNode(null);
      message.success('Node configuration saved');
    } catch (err) {
      // validation error
    }
  };

  // Handle condition edit
  const onConditionEdit = () => {
    if (selectedNode?.type === 'condition') {
      setConditionModalVisible(true);
    }
  };

  const saveTicketBinding = async () => {
    if (!workflowId) return;
    setBindingSaving(true);
    try {
      const values = await bindingForm.validateFields();
      const labelFilters = normalizeBindingLabelFilters(values.label_filters);
      const labelFilterLogic: TicketWorkflowBinding['label_filter_logic'] = values.label_filter_logic === 'OR' ? 'OR' : 'AND';
      const payload: Partial<TicketWorkflowBinding> = {
        name: values.name,
        workflow: workflowId,
        label_filter_logic: labelFilterLogic,
        label_filters: labelFilters,
      };

      const saved = ticketBinding
        ? await updateTicketWorkflowBinding(ticketBinding.id, payload)
        : await createTicketWorkflowBinding(payload);
      setTicketBinding(saved);
      bindingForm.setFieldsValue({
        name: saved.name,
        label_filter_logic: saved.label_filter_logic,
        label_filters: saved.label_filters?.length ? saved.label_filters : [{ label_name: '', label_value: '' }],
      });
      message.success('Ticket label binding saved');
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(getApiErrorMessage(err, 'Failed to save ticket label binding'));
    } finally {
      setBindingSaving(false);
    }
  };

  const removeTicketBinding = async () => {
    if (!ticketBinding) return;
    setBindingSaving(true);
    try {
      await deleteTicketWorkflowBinding(ticketBinding.id);
      setTicketBinding(null);
      bindingForm.setFieldsValue({
        name: `${workflow?.name || 'Workflow'} ticket binding`,
        label_filter_logic: 'AND',
        label_filters: [{ label_name: '', label_value: '' }],
      });
      message.success('Ticket label binding deleted');
    } catch (err: any) {
      message.error(getApiErrorMessage(err, 'Failed to delete ticket label binding'));
    } finally {
      setBindingSaving(false);
    }
  };

  const saveCondition = (condition: Record<string, any>) => {
    if (!selectedNode) return;

    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === selectedNode.id) {
          return {
            ...node,
            data: {
              ...node.data,
              condition,
            },
          };
        }
        return node;
      })
    );

    setSelectedNode((prev) => {
      if (!prev || String(prev.id) !== String(selectedNode.id)) return prev;
      return {
        ...prev,
        data: {
          ...prev.data,
          condition,
        },
      };
    });

    setConditionModalVisible(false);
    message.success('Condition saved');
  };

  // Delete selected node
  const deleteSelectedNode = () => {
    if (!selectedNode) return;

    const nodeId = String(selectedNode.id);

    setNodes((nds) => nds.filter((n) => String(n.id) !== nodeId));
    setEdges((eds) =>
      eds.filter((e) => String(e.source) !== nodeId && String(e.target) !== nodeId)
    );
    setNodeDrawerVisible(false);
    setSelectedNode(null);
  };

  const openDeleteConfirm = () => {
    if (!selectedNode) return;
    setDeleteConfirmVisible(true);
  };

  const closeDeleteConfirm = () => {
    setDeleteConfirmVisible(false);
  };

  // Save workflow
  const handleSave = async (activate: boolean = false) => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      const tags = normalizeTags(values.tags);

      const nodeIdSet = new Set(nodes.map((node) => node.id));
      const sanitizedEdges = edges.filter(
        (edge) => nodeIdSet.has(edge.source) && nodeIdSet.has(edge.target)
      );

      const steps = nodesToSteps(nodes, sanitizedEdges);
      const workflowEdges = flowEdgesToWorkflowEdges(sanitizedEdges);

      const payload: Partial<Workflow> = {
        name: values.name,
        description: values.description || '',
        trigger_type: values.trigger_type,
        trigger_conditions: buildTriggerConditions(values),
        schedule_cron: values.schedule_cron || null,
        is_active: activate || values.is_active,
        is_draft: !activate && values.is_draft,
        tags,
        edges: workflowEdges,
        steps,
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

  const handleSaveAsPermanentNode = async () => {
    if (!selectedNode) return;
    if (selectedNode.type === 'start' || selectedNode.type === 'end') {
      message.warning('Start/End nodes cannot be saved as reusable nodes');
      return;
    }

    try {
      const values = await nodeForm.validateFields();
      const currentNode = getCurrentNodeById(String(selectedNode.id)) || selectedNode;
      const normalizedCategory = normalizeCategoryValue(
        values.node_category,
        currentNode.type === 'condition' ? 'control' : currentNode.data.category || 'utility'
      );
      await createSavedWorkflowNode({
        name: values.name || currentNode.data.label,
        node_type: (currentNode.type === 'action' ? 'action' : currentNode.type) as any,
        node_category: normalizedCategory,
        action_type: currentNode.data.actionType || '',
        action_config: actionConfig,
        timeout_seconds: values.timeout_seconds || 300,
        on_failure: values.on_failure || 'stop',
        retry_count: values.retry_count || 0,
        retry_delay_seconds: values.retry_delay_seconds || 30,
        condition: currentNode.data.condition || {},
        is_active: values.is_active !== false,
      });
      await refreshSavedNodes();
      message.success('Node saved to Workflow Nodes');
    } catch {
      message.error('Failed to save node');
    }
  };

  const openSaveAsConfirm = () => {
    setSaveAsConfirmVisible(true);
  };

  const closeSaveAsConfirm = () => {
    setSaveAsConfirmVisible(false);
  };

  const openCreateSavedNodeForm = () => {
    setEditingSavedNode(null);
    savedNodeForm.setFieldsValue({
      name: '',
      node_type: 'action',
      node_category: 'utility',
      action_type: '',
      timeout_seconds: 300,
      on_failure: 'stop',
      retry_count: 0,
      retry_delay_seconds: 30,
      is_active: true,
    });
    setSavedNodeFormVisible(true);
  };

  const openEditSavedNodeForm = (node: SavedWorkflowNode) => {
    setEditingSavedNode(node);
    savedNodeForm.setFieldsValue({
      name: node.name,
      node_type: node.node_type,
      node_category: node.node_category,
      action_type: node.action_type || '',
      timeout_seconds: node.timeout_seconds,
      on_failure: node.on_failure,
      retry_count: node.retry_count,
      retry_delay_seconds: node.retry_delay_seconds,
      is_active: node.is_active,
    });
    setSavedNodeFormVisible(true);
  };

  const handleSaveManagedSavedNode = async () => {
    try {
      const values = await savedNodeForm.validateFields();
      const payload = {
        name: values.name,
        node_type: values.node_type,
        node_category: normalizeCategoryValue(values.node_category, 'utility'),
        action_type: values.action_type || '',
        timeout_seconds: values.timeout_seconds || 300,
        on_failure: values.on_failure || 'stop',
        retry_count: values.retry_count || 0,
        retry_delay_seconds: values.retry_delay_seconds || 30,
        action_config: editingSavedNode?.action_config || {},
        condition: editingSavedNode?.condition || {},
        is_active: values.is_active !== false,
      };

      if (editingSavedNode) {
        await updateSavedWorkflowNode(editingSavedNode.id, payload);
        message.success('Saved node updated');
      } else {
        await createSavedWorkflowNode(payload);
        message.success('Saved node created');
      }

      setSavedNodeFormVisible(false);
      setEditingSavedNode(null);
      await refreshSavedNodes();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(getApiErrorMessage(err, 'Failed to save custom node'));
    }
  };

  const handleDeleteManagedSavedNode = async (node: SavedWorkflowNode) => {
    try {
      await deleteSavedWorkflowNode(node.id);
      await refreshSavedNodes();
      message.success('Saved node deleted');
    } catch (err: any) {
      message.error(getApiErrorMessage(err, 'Failed to delete saved node'));
    }
  };

  // Execute workflow
  const handleExecute = async () => {
    if (!workflowId) return;
    try {
      await executeWorkflow(workflowId);
      message.success('Workflow execution started');
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
              await executeWorkflow(workflowId, {}, true);
              message.success('Workflow execution started');
            } catch (confirmErr: any) {
              message.error(getApiErrorMessage(confirmErr, 'Failed to execute workflow'));
            }
          },
        });
        return;
      }

      message.error(err.response?.data?.error || 'Failed to execute workflow');
    }
  };

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }} className="workflow-editor">
      {modalContextHolder}
      {/* Header */}
      <Card
        style={{ borderRadius: 0, borderBottom: '1px solid var(--workflow-border, #f0f0f0)' }}
        styles={{ body: { padding: '12px 24px' } }}
      >
        <Row justify="space-between" align="middle">
          <Col>
            <Space>
              <Button icon={<ArrowLeftOutlined />} onClick={onBack}>
                Back
              </Button>
              <span style={{ fontSize: 18, fontWeight: 600 }}>
                {isNew ? 'Create Visual Workflow' : `Edit: ${workflow?.name || ''}`}
              </span>
              {workflow && <Tag color="blue">v{workflow.version}</Tag>}
              {workflow?.published_version ? (
                <Tag color="green" title={workflow.published_at ? `Published at ${workflow.published_at}` : undefined}>
                  Published v{workflow.published_version}
                </Tag>
              ) : (
                workflow ? <Tag>Unpublished</Tag> : null
              )}
              {workflow?.has_unpublished_changes ? (
                <Tag color="red" title="Workflow has been modified since the last publish.">Unpublished Changes</Tag>
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
              <Button icon={<SaveOutlined />} onClick={() => handleSave(false)} loading={saving}>
                Save Draft
              </Button>
              <Button type="primary" icon={<CheckOutlined />} onClick={() => handleSave(true)} loading={saving}>
                Save & Activate
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* Main Content */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Left Sidebar - Settings & Actions */}
        <div
          style={{
            width: 280,
            background: 'var(--workflow-sidebar-bg, #fafafa)',
            borderRight: '1px solid var(--workflow-border, #f0f0f0)',
            overflow: 'auto',
            padding: 16,
          }}
        >
          <Card title="Workflow Settings" size="small" style={{ marginBottom: 16 }}>
            <Form form={form} layout="vertical" size="small">
              <Form.Item name="name" label="Name" rules={[{ required: true }]}>
                <Input placeholder="Workflow Name" />
              </Form.Item>
              <Form.Item name="description" label="Description">
                <Input.TextArea rows={2} placeholder="Description..." />
              </Form.Item>
              <Form.Item name="trigger_type" label="Trigger" rules={[{ required: true }]}>
                <Select
                  options={triggerTypes}
                  onChange={handleTriggerTypeChange}
                />
              </Form.Item>
              <Form.Item noStyle shouldUpdate={(prev, curr) => prev.trigger_type !== curr.trigger_type}>
                {({ getFieldValue }) =>
                  getFieldValue('trigger_type') === 'scheduled' && (
                    <Form.Item name="schedule_cron" label="Cron">
                      <Input placeholder="0 */4 * * *" />
                    </Form.Item>
                  )
                }
              </Form.Item>
              <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
                Trigger details are configured in Start Node {'->'} Configure Node.
              </Text>
              <Row gutter={8}>
                <Col span={12}>
                  <Form.Item noStyle shouldUpdate={(prev, curr) => prev.is_active !== curr.is_active}>
                    {({ getFieldValue, setFieldValue }) => (
                      <Form.Item label="Active">
                        <Switch
                          size="small"
                          checked={Boolean(getFieldValue('is_active'))}
                          onChange={(checked) => setFieldValue('is_active', checked)}
                        />
                      </Form.Item>
                    )}
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item noStyle shouldUpdate={(prev, curr) => prev.is_draft !== curr.is_draft}>
                    {({ getFieldValue, setFieldValue }) => (
                      <Form.Item label="Draft">
                        <Switch
                          size="small"
                          checked={Boolean(getFieldValue('is_draft'))}
                          onChange={(checked) => setFieldValue('is_draft', checked)}
                        />
                      </Form.Item>
                    )}
                  </Form.Item>
                </Col>
              </Row>
            </Form>
          </Card>

          {!isNew && (
            <Card
              title={
                <Space>
                  <TagsOutlined />
                  Ticket Labels
                </Space>
              }
              size="small"
              style={{ marginBottom: 16 }}
              loading={bindingLoading}
            >
              <Form form={bindingForm} layout="vertical" size="small" preserve={false}>
                <Form.Item name="name" label="Binding Name" rules={[{ required: true, message: 'Binding name is required' }]}>
                  <Input placeholder="e.g. Critical ticket routing" />
                </Form.Item>
                <Form.Item name="label_filter_logic" label="Label Logic" rules={[{ required: true }]}>
                  <Select
                    options={[
                      { value: 'AND', label: 'AND' },
                      { value: 'OR', label: 'OR' },
                    ]}
                  />
                </Form.Item>
                <Form.List name="label_filters">
                  {(fields, { add, remove }) => (
                    <div>
                      {fields.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No labels" />}
                      {fields.map((field) => (
                        <div
                          key={field.key}
                          style={{
                            padding: 8,
                            marginBottom: 8,
                            border: '1px solid var(--workflow-border, #f0f0f0)',
                            borderRadius: 6,
                          }}
                        >
                          <Form.Item
                            {...field}
                            name={[field.name, 'label_name']}
                            rules={[{ required: true, message: 'Required' }]}
                            style={{ marginBottom: 8 }}
                          >
                            <Input placeholder="label_name" />
                          </Form.Item>
                          <Form.Item {...field} name={[field.name, 'label_value']} style={{ marginBottom: 8 }}>
                            <Input placeholder="label_value" />
                          </Form.Item>
                          <Button danger size="small" block onClick={() => remove(field.name)}>
                            Remove
                          </Button>
                        </div>
                      ))}
                      <Button size="small" onClick={() => add({ label_name: '', label_value: '' })} style={{ marginBottom: 12 }}>
                        Add Label
                      </Button>
                    </div>
                  )}
                </Form.List>
                <Space wrap>
                  <Button type="primary" size="small" loading={bindingSaving} onClick={saveTicketBinding}>
                    Save Labels
                  </Button>
                  {ticketBinding && (
                    <Popconfirm
                      title="Delete this ticket label binding?"
                      onConfirm={removeTicketBinding}
                      okText="Yes"
                      cancelText="No"
                    >
                      <Button danger size="small" loading={bindingSaving}>
                        Delete Binding
                      </Button>
                    </Popconfirm>
                  )}
                </Space>
              </Form>
            </Card>
          )}

          <ActionPalette
            actions={availableActions}
            savedNodes={savedNodes}
            onManageSavedNodes={() => setSavedNodesManagerVisible(true)}
          />
        </div>

        {/* Canvas */}
        <div ref={reactFlowWrapper} style={{ flex: 1 }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodesDelete={onNodesDelete}
            onEdgesDelete={onEdgesDelete}
            onEdgeDoubleClick={onEdgeDoubleClick}
            onConnect={onConnect}
            onInit={setReactFlowInstance}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onNodeClick={onNodeClick}
            nodeTypes={nodeTypes}
            fitView
            snapToGrid
            snapGrid={[15, 15]}
            // Accept both keys so Windows users (Delete) and Mac users
            // (Backspace) can remove the selected edge/node consistently.
            deleteKeyCode={['Delete', 'Backspace']}
            edgesFocusable
            elementsSelectable
            defaultEdgeOptions={{
              type: 'smoothstep',
              markerEnd: { type: MarkerType.ArrowClosed },
            }}
          >
            <Controls />
            <MiniMap
              nodeStrokeWidth={3}
              zoomable
              pannable
              style={{ background: 'var(--workflow-minimap-bg, #f5f5f5)' }}
            />
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
          </ReactFlow>
        </div>
      </div>

      {/* Node Configuration Drawer */}
      <Drawer
        title={
          <Space>
            <SettingOutlined />
            Configure Node
          </Space>
        }
        placement="right"
        open={nodeDrawerVisible}
        onClose={() => {
          setNodeDrawerVisible(false);
          setSelectedNode(null);
        }}
        width={500}
        extra={
          <Space>
            <Button danger icon={<DeleteOutlined />} onClick={openDeleteConfirm}>
              Delete
            </Button>
            {(selectedNode?.type === 'action' || selectedNode?.type === 'condition') && (
              <Button icon={<SaveOutlined />} onClick={openSaveAsConfirm}>
                Save As
              </Button>
            )}
            <Button type="primary" onClick={saveNodeConfig}>
              Save
            </Button>
          </Space>
        }
      >
        {selectedNode && (
          <Form form={nodeForm} layout="vertical">
            <Form.Item name="name" label="Node Name" rules={[{ required: true }]}>
              <Input />
            </Form.Item>

            {/* Start/End node - minimal config */}
            {(selectedNode.type === 'start' || selectedNode.type === 'end') && (
              <Card
                size="small"
                style={{
                  background: 'var(--workflow-muted-card-bg, #f5f5f5)',
                  marginBottom: 16,
                  borderColor: 'var(--workflow-border, #f0f0f0)',
                }}
              >
                <Text type="secondary">
                  {selectedNode.type === 'start'
                    ? 'This is the workflow entry point. You can rename it and configure trigger options here.'
                    : 'This is the workflow termination point. You can rename or delete it.'}
                </Text>
              </Card>
            )}

            {selectedNode.type === 'start' && (
              <>
                <Divider>Start Trigger Configuration</Divider>
                <Form form={form} layout="vertical" size="small">
                  <Form.Item name="trigger_type" label="Trigger" rules={[{ required: true }]}>
                    <Select options={triggerTypes} onChange={handleTriggerTypeChange} />
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

                  <Form.Item noStyle shouldUpdate={(prev, curr) => prev.trigger_type !== curr.trigger_type || prev.alert_filters !== curr.alert_filters || prev.alert_filter_logic !== curr.alert_filter_logic}>
                    {({ getFieldValue }) =>
                      getFieldValue('trigger_type') === 'alert' && (
                        <>
                          <Form.Item name="alert_filter_logic" label="Condition Logic" initialValue="AND">
                            <Select options={triggerLogicOptions} />
                          </Form.Item>
                          <Form.List name="alert_filters">
                            {(fields, { add, remove }) => (
                              <>
                                <Text type="secondary">Alert conditions</Text>
                                {fields.map((field) => (
                                  <Row gutter={8} key={field.key} style={{ marginTop: 8 }}>
                                    <Col span={8}>
                                      <Form.Item
                                        name={[field.name, 'field']}
                                        rules={[{ required: true, message: 'Field required' }]}
                                      >
                                        <Select options={alertTriggerFieldOptions} placeholder="Field" />
                                      </Form.Item>
                                    </Col>
                                    <Col span={6}>
                                      <Form.Item
                                        name={[field.name, 'operator']}
                                        initialValue="=="
                                        rules={[{ required: true }]}
                                      >
                                        <Select options={triggerOperatorOptions} placeholder="Operator" />
                                      </Form.Item>
                                    </Col>
                                    <Col span={8}>
                                      <Form.Item
                                        name={[field.name, 'value']}
                                        rules={[{ required: true, message: 'Value required' }]}
                                      >
                                        <Input placeholder="Value" />
                                      </Form.Item>
                                    </Col>
                                    <Col span={2}>
                                      <Button danger type="text" onClick={() => remove(field.name)}>
                                        Remove
                                      </Button>
                                    </Col>
                                  </Row>
                                ))}
                                <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({ operator: '==' })} block>
                                  Add Alert Condition
                                </Button>
                              </>
                            )}
                          </Form.List>
                        </>
                      )
                    }
                  </Form.Item>

                  <Form.Item noStyle shouldUpdate={(prev, curr) => prev.trigger_type !== curr.trigger_type || prev.ticket_filters !== curr.ticket_filters || prev.ticket_filter_logic !== curr.ticket_filter_logic}>
                    {({ getFieldValue }) =>
                      getFieldValue('trigger_type') === 'ticket_created' && (
                        <>
                          <Form.Item name="ticket_filter_logic" label="Condition Logic" initialValue="AND">
                            <Select options={triggerLogicOptions} />
                          </Form.Item>
                          <Form.List name="ticket_filters">
                            {(fields, { add, remove }) => (
                              <>
                                <Text type="secondary">Ticket conditions</Text>
                                {fields.map((field) => (
                                  <Row gutter={8} key={field.key} style={{ marginTop: 8 }}>
                                    <Col span={8}>
                                      <Form.Item
                                        name={[field.name, 'field']}
                                        rules={[{ required: true, message: 'Field required' }]}
                                      >
                                        <Select options={ticketTriggerFieldOptions} placeholder="Field" />
                                      </Form.Item>
                                    </Col>
                                    <Col span={6}>
                                      <Form.Item
                                        name={[field.name, 'operator']}
                                        initialValue="=="
                                        rules={[{ required: true }]}
                                      >
                                        <Select options={triggerOperatorOptions} placeholder="Operator" />
                                      </Form.Item>
                                    </Col>
                                    <Col span={8}>
                                      <Form.Item shouldUpdate noStyle>
                                        {({ getFieldValue: getStartFieldValue }) => {
                                          const selectedField = getStartFieldValue(['ticket_filters', field.name, 'field']);
                                          const valueOptions = ticketFieldValueOptions[selectedField] || [];
                                          return (
                                            <Form.Item
                                              name={[field.name, 'value']}
                                              rules={[{ required: true, message: 'Value required' }]}
                                            >
                                              {valueOptions.length ? (
                                                <Select options={valueOptions} placeholder={selectedField === 'labels' ? 'name:value' : 'Value'} />
                                              ) : (
                                                <Input placeholder={selectedField === 'labels' ? 'name:value (e.g. env:prod)' : 'Value'} />
                                              )}
                                            </Form.Item>
                                          );
                                        }}
                                      </Form.Item>
                                    </Col>
                                    <Col span={2}>
                                      <Button danger type="text" onClick={() => remove(field.name)}>
                                        Remove
                                      </Button>
                                    </Col>
                                  </Row>
                                ))}
                                <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({ operator: '==' })} block>
                                  Add Ticket Condition
                                </Button>
                              </>
                            )}
                          </Form.List>
                        </>
                      )
                    }
                  </Form.Item>
                </Form>
              </>
            )}

            {selectedNode.type === 'action' && (
              <>
                <Form.Item name="node_category" label="Category" rules={[{ required: true }]}> 
                  <Select
                    showSearch
                    options={categoryOptions.filter((item) => item.value !== 'control')}
                    placeholder="Select category"
                  />
                </Form.Item>
                <Form.Item label="Action Type">
                  <Input value={selectedNode.data.actionType} disabled />
                </Form.Item>
                <Divider>Action Configuration</Divider>
                <ActionConfigBuilder
                  actionType={selectedNode.data.actionType || ''}
                  actionInfo={availableActions.find(
                    (action) => action.action_type === selectedNode.data.actionType
                  )}
                  config={actionConfig}
                  configKey={String(selectedNode.id)}
                  configuredSecretFields={selectedNode.data.configuredSecretFields || []}
                  onChange={(newConfig) => {
                    setActionConfig(newConfig);
                  }}
                />
                <Divider>Execution Settings</Divider>
                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item name="timeout_seconds" label="Timeout (s)">
                      <InputNumber min={1} max={3601} style={{ width: '100%' }} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item name="on_failure" label="On Failure">
                      <Select options={onFailureOptions} />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item name="retry_count" label="Retry Count">
                  <InputNumber min={0} max={5} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item name="retry_delay_seconds" label="Retry Delay (s)">
                  <InputNumber min={0} max={3600} style={{ width: '100%' }} />
                </Form.Item>
              </>
            )}

            {selectedNode.type === 'condition' && (
              <>
                <Form.Item name="node_category" label="Category" rules={[{ required: true }]}>
                  <Select showSearch options={categoryOptions} placeholder="Select category" />
                </Form.Item>
                <Divider>Condition Configuration</Divider>
                <Form.Item label="Current Condition">
                  <Card
                    size="small"
                    style={{
                      background: 'var(--workflow-muted-card-bg, #f5f5f5)',
                      borderColor: 'var(--workflow-border, #f0f0f0)',
                    }}
                  >
                    <code style={{ fontSize: 12 }}>
                      {JSON.stringify(selectedNode.data.condition || {}, null, 2)}
                    </code>
                  </Card>
                </Form.Item>
                <Button
                  type="primary"
                  icon={<BranchesOutlined />}
                  onClick={onConditionEdit}
                  block
                >
                  Edit Condition
                </Button>
              </>
            )}

            <Divider />
            <Form.Item noStyle shouldUpdate={(prev, curr) => prev.is_active !== curr.is_active}>
              {({ getFieldValue, setFieldValue }) => (
                <Form.Item label="Active">
                  <Switch
                    checked={getFieldValue('is_active') !== false}
                    onChange={(checked) => setFieldValue('is_active', checked)}
                  />
                </Form.Item>
              )}
            </Form.Item>
          </Form>
        )}
      </Drawer>

      {/* Condition Builder Modal */}
      <ConditionBuilder
        visible={conditionModalVisible}
        condition={selectedNode?.data?.condition}
        onSave={saveCondition}
        onCancel={() => setConditionModalVisible(false)}
      />

      <Modal
        title="Save as reusable node?"
        open={saveAsConfirmVisible}
        onCancel={closeSaveAsConfirm}
        onOk={async () => {
          await handleSaveAsPermanentNode();
          closeSaveAsConfirm();
        }}
        okText="Save"
        cancelText="Cancel"
      >
        <p>
          This will permanently save "{selectedNode?.data?.label || 'this node'}" to Workflow Nodes for future reuse.
        </p>
      </Modal>

      <Modal
        title="Edit Workflow Nodes"
        open={savedNodesManagerVisible}
        onCancel={() => setSavedNodesManagerVisible(false)}
        footer={[
          <Button key="close" onClick={() => setSavedNodesManagerVisible(false)}>
            Close
          </Button>,
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreateSavedNodeForm}>
            Add Node
          </Button>,
        ]}
        width={760}
      >
        <Text type="secondary">
          Only custom nodes saved via "Save As" can be edited here. System default nodes are not editable.
        </Text>
        <List
          style={{ marginTop: 16 }}
          dataSource={savedNodes}
          locale={{ emptyText: 'No custom workflow nodes yet' }}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button key="edit" type="link" icon={<EditOutlined />} onClick={() => openEditSavedNodeForm(item)}>
                  Edit
                </Button>,
                <Popconfirm
                  key="delete"
                  title="Delete saved node?"
                  description={`This will remove reusable node "${item.name}".`}
                  okText="Delete"
                  cancelText="Cancel"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => handleDeleteManagedSavedNode(item)}
                >
                  <Button type="link" danger>
                    Delete
                  </Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <span>{item.name}</span>
                    <Tag>{item.node_type}</Tag>
                    <Tag color="blue">{toCategoryLabel(item.node_category)}</Tag>
                  </Space>
                }
                description={item.action_type ? `Action: ${item.action_type}` : 'Condition/Custom node'}
              />
            </List.Item>
          )}
        />
      </Modal>

      <Modal
        title={editingSavedNode ? 'Edit Custom Node' : 'Add Custom Node'}
        open={savedNodeFormVisible}
        onCancel={() => {
          setSavedNodeFormVisible(false);
          setEditingSavedNode(null);
        }}
        onOk={handleSaveManagedSavedNode}
        okText={editingSavedNode ? 'Update' : 'Create'}
      >
        <Form form={savedNodeForm} layout="vertical">
          <Form.Item name="name" label="Node Name" rules={[{ required: true }]}> 
            <Input placeholder="Custom node name" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="node_type" label="Node Type" rules={[{ required: true }]}> 
                <Select
                  options={[
                    { value: 'action', label: 'Action' },
                    { value: 'condition', label: 'Condition' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="node_category" label="Category" rules={[{ required: true }]}> 
                <AutoComplete options={categoryOptions}>
                  <Input placeholder="Select or type a new category" />
                </AutoComplete>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item noStyle shouldUpdate={(prev, curr) => prev.node_type !== curr.node_type}>
            {({ getFieldValue }) =>
              getFieldValue('node_type') === 'action' ? (
                <Form.Item
                  name="action_type"
                  label="Action Type"
                  rules={[{ required: true, message: 'Action node requires action type' }]}
                >
                  <AutoComplete
                    options={availableActions.map((action) => ({
                      value: action.action_type,
                      label: `${action.name} (${action.action_type})`,
                    }))}
                  >
                    <Input placeholder="e.g. send_email" />
                  </AutoComplete>
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="timeout_seconds" label="Timeout (s)">
                <InputNumber min={1} max={3601} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="retry_count" label="Retry">
                <InputNumber min={0} max={5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="retry_delay_seconds" label="Retry Delay (s)">
                <InputNumber min={0} max={3600} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="on_failure" label="On Failure">
                <Select options={onFailureOptions} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item noStyle shouldUpdate={(prev, curr) => prev.is_active !== curr.is_active}>
            {({ getFieldValue, setFieldValue }) => (
              <Form.Item label="Active">
                <Switch
                  checked={getFieldValue('is_active') !== false}
                  onChange={(checked) => setFieldValue('is_active', checked)}
                />
              </Form.Item>
            )}
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Delete Node"
        open={deleteConfirmVisible}
        onOk={() => {
          deleteSelectedNode();
          closeDeleteConfirm();
        }}
        onCancel={closeDeleteConfirm}
        okText="Delete"
        okButtonProps={{ danger: true }}
      >
        <p>Are you sure you want to delete "{selectedNode?.data?.label || 'this node'}"?</p>
      </Modal>
    </div>
  );
};

// Wrap with ReactFlowProvider
const VisualWorkflowEditorWrapper: React.FC<VisualWorkflowEditorProps> = (props) => {
  return (
    <ReactFlowProvider>
      <VisualWorkflowEditor {...props} />
    </ReactFlowProvider>
  );
};

export default VisualWorkflowEditorWrapper;
