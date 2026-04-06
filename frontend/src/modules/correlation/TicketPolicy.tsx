import React, { useEffect, useMemo, useState } from 'react';
import { Button, Card, Form, Input, List, Modal, Select, Space, Tag, message, Divider, Typography } from 'antd';
import { createTicketPolicy, deleteTicketPolicy, fetchTicketPolicies, updateTicketPolicy } from 'services/ticketPolicies';
import { listIntegrations, integrationsPreviewEsMapping } from 'services/integrations';
import type { TicketPolicy } from '../../types';

const POLICY_LABELS: Record<TicketPolicy['policy_type'], string> = {
  creation: 'Ticket Creation Conditions',
};

const { Text } = Typography;

const OPERATORS = [
  { label: 'equals', value: 'eq', needsValue: true },
  { label: 'not equals', value: 'ne', needsValue: true },
  { label: 'contains', value: 'contains', needsValue: true },
  { label: 'in list', value: 'in', needsValue: true },
  { label: 'greater than', value: 'gt', needsValue: true },
  { label: 'greater or equal', value: 'gte', needsValue: true },
  { label: 'less than', value: 'lt', needsValue: true },
  { label: 'less or equal', value: 'lte', needsValue: true },
  { label: 'exists', value: 'exists', needsValue: false },
  { label: 'regex', value: 'regex', needsValue: true },
];

const makeRuleId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const formatContent = (content: any) => {
  try {
    return JSON.stringify(content ?? {}, null, 2);
  } catch {
    return String(content ?? '');
  }
};

type RuleItem = {
  id: string;
  field: string;
  op: string;
  value?: string;
};

export default function TicketPolicyPage({ embedded = false }: { embedded?: boolean }) {
  const [items, setItems] = useState<TicketPolicy[]>([]);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<TicketPolicy | null>(null);
  const [integrations, setIntegrations] = useState<any[]>([]);
  const [conditionLogic, setConditionLogic] = useState<'AND' | 'OR'>('AND');
  const [rules, setRules] = useState<RuleItem[]>([{ id: makeRuleId(), field: '', op: 'eq', value: '' }]);
  const [esFields, setEsFields] = useState<string[]>([]);
  const [loadingFields, setLoadingFields] = useState(false);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const res = await fetchTicketPolicies();
      setItems(Array.isArray(res) ? res : (res?.results ?? []));
    } catch (err: any) {
      message.error(err?.message || 'Failed to load policies');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchAll(); }, []);
  useEffect(() => {
    const load = async () => {
      try {
        const res = await listIntegrations();
        setIntegrations(Array.isArray(res) ? res : (res?.results ?? []));
      } catch {
        setIntegrations([]);
      }
    };
    load();
  }, []);

  const applyConditionsToState = (conditions: any) => {
    if (!conditions || !Array.isArray(conditions.rules)) {
      setConditionLogic('AND');
      setRules([{ id: makeRuleId(), field: '', op: 'eq', value: '' }]);
      return;
    }
    const logic = conditions.logic === 'OR' ? 'OR' : 'AND';
    const nextRules = conditions.rules.map((r: any) => ({
      id: makeRuleId(),
      field: r.field || r.key || '',
      op: r.op || r.operator || 'eq',
      value: Array.isArray(r.value) ? r.value.join(',') : (r.value ?? ''),
    }));
    setConditionLogic(logic);
    setRules(nextRules.length ? nextRules : [{ id: makeRuleId(), field: '', op: 'eq', value: '' }]);
  };

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ policy_type: 'creation' });
    applyConditionsToState(null);
    setEsFields([]);
    setModalOpen(true);
  };

  const openEdit = (policy: TicketPolicy) => {
    setEditing(policy);
    const content = policy.content || {};
    form.setFieldsValue({
      name: policy.name,
      policy_type: policy.policy_type,
      alerts_id: content.es_source?.integration_id,
      es_index: content.es_source?.index,
    });
    applyConditionsToState(content.conditions);
    setEsFields([]);
    setModalOpen(true);
  };

  const handleDelete = (policy: TicketPolicy) => {
    Modal.confirm({
      title: 'Delete policy?',
      content: policy.name,
      okText: 'Delete',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteTicketPolicy(String(policy.id));
          message.success('Policy deleted');
          fetchAll();
        } catch (err: any) {
          message.error(err?.message || 'Delete failed');
        }
      },
    });
  };

  const loadEsFields = async () => {
    const values = form.getFieldsValue(['alerts_id', 'es_index']);
    if (!values.alerts_id || !values.es_index) {
      message.error('Select ES integration and index first');
      return;
    }
    setLoadingFields(true);
    try {
      const res = await integrationsPreviewEsMapping({
        alerts: values.alerts_id,
        index: values.es_index,
        size: 1,
      });
      const cols = Array.isArray(res?.columns) ? res.columns : [];
      const fields = cols.map((c: any) => c.orig_name).filter(Boolean);
      setEsFields(Array.from(new Set(fields)));
      if (!fields.length) {
        message.warning('No fields returned from ES mapping');
      }
    } catch (err: any) {
      message.error(err?.message || 'Failed to load ES fields');
      setEsFields([]);
    } finally {
      setLoadingFields(false);
    }
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const normalizedRules = rules.map((r) => {
        let value: any = r.value;
        let op = r.op;
        if (op === 'exists') value = true;
        if (op === 'in' && typeof value === 'string') {
          value = value.split(',').map((v) => v.trim()).filter(Boolean);
        }
        if (op === 'eq' && Array.isArray(value)) {
          op = 'in';
        }
        if (['gt', 'gte', 'lt', 'lte'].includes(op) && typeof value === 'string') {
          const parsed = Number(value);
          if (!Number.isNaN(parsed)) value = parsed;
        }
        return {
          field: r.field?.trim(),
          op,
          value,
        };
      });
      for (const r of normalizedRules) {
        if (!r.field) {
          message.error('Each rule needs a field');
          return;
        }
        const opMeta = OPERATORS.find((o) => o.value === r.op);
        const valueMissing = r.value === undefined || r.value === null || String(r.value).trim() === '';
        const listMissing = Array.isArray(r.value) && r.value.length === 0;
        if (opMeta?.needsValue && (valueMissing || listMissing)) {
          message.error(`Operator "${r.op}" needs a value`);
          return;
        }
      }
      const conditions = {
        logic: conditionLogic,
        rules: normalizedRules.map((r) => ({
          field: r.field,
          op: r.op,
          value: r.value,
        })),
      };
      const payload = {
        name: values.name.trim(),
        policy_type: values.policy_type,
        content: {
          conditions,
          es_source: {
            integration_id: values.alerts_id || null,
            index: values.es_index || null,
          },
        },
      };
      if (editing) {
        await updateTicketPolicy(String(editing.id), payload);
        message.success('Policy updated');
      } else {
        await createTicketPolicy(payload);
        message.success('Policy created');
      }
      setModalOpen(false);
      setEditing(null);
      fetchAll();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err?.message || 'Save failed');
    }
  };

  const typeTag = (t: TicketPolicy['policy_type']) => (
    <Tag color="blue">{POLICY_LABELS[t]}</Tag>
  );

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => (a.updated_at || '').localeCompare(b.updated_at || '')).reverse();
  }, [items]);

  return (
    <div style={{ padding: embedded ? 0 : 12 }}>
      <Card
        title="Ticket Policy"
        extra={<Button type="primary" onClick={openCreate}>New Policy</Button>}
      >
        <List
          loading={loading}
          dataSource={sortedItems}
          renderItem={(policy) => (
            <List.Item
              actions={[
                <Button key="edit" onClick={() => openEdit(policy)}>Edit</Button>,
                <Button key="delete" danger onClick={() => handleDelete(policy)}>Delete</Button>,
              ]}
            >
              <List.Item.Meta
                title={<Space>{policy.name}{typeTag(policy.policy_type)}</Space>}
                description={`Updated: ${policy.updated_at || '-'}`}
              />
            </List.Item>
          )}
        />
      </Card>

      <Modal
        title={editing ? 'Edit Policy' : 'New Policy'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        destroyOnClose
        width={720}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="policy_type" label="Type" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'creation', label: 'Ticket Creation Conditions' },
              ]}
              disabled
            />
          </Form.Item>
          <Form.Item name="alerts_id" label="ES Integration">
            <Select
              allowClear
              placeholder="Select integration"
              options={integrations.filter((i) => (i.type || '').toString().toLowerCase() === 'elasticsearch').map((i) => ({
                value: i.id,
                label: i.name || i.id,
              }))}
            />
          </Form.Item>
          <Form.Item name="es_index" label="ES Index">
            <Input placeholder="alerts" />
          </Form.Item>
          <Space style={{ marginBottom: 12 }}>
            <Button onClick={loadEsFields} loading={loadingFields}>Load ES Fields</Button>
            <Text type="secondary">{esFields.length ? `${esFields.length} fields loaded` : 'No fields loaded'}</Text>
          </Space>
          <Divider />
          <Form.Item label="Ticket Creation Conditions">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space>
                <Text>Match</Text>
                <Select
                  value={conditionLogic}
                  onChange={(v) => setConditionLogic(v)}
                  options={[
                    { label: 'ALL (AND)', value: 'AND' },
                    { label: 'ANY (OR)', value: 'OR' },
                  ]}
                  style={{ width: 160 }}
                />
                <Text>rules</Text>
              </Space>
              {rules.map((rule) => {
                const opMeta = OPERATORS.find((o) => o.value === rule.op);
                return (
                  <div key={rule.id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <Select
                      placeholder={esFields.length ? 'Field' : 'Load ES fields first'}
                      showSearch
                      style={{ width: 200 }}
                      value={rule.field || undefined}
                      onChange={(v) => setRules((prev) => prev.map((r) => r.id === rule.id ? { ...r, field: v } : r))}
                      options={esFields.map((f) => ({ label: f, value: f }))}
                      disabled={!esFields.length}
                    />
                    <Select
                      style={{ width: 160 }}
                      value={rule.op}
                      onChange={(v) => setRules((prev) => prev.map((r) => r.id === rule.id ? { ...r, op: v } : r))}
                      options={OPERATORS.map((o) => ({ label: o.label, value: o.value }))}
                    />
                    {opMeta?.needsValue ? (
                      <Input
                        placeholder={rule.op === 'in' ? 'comma,separated,values' : rule.op === 'regex' ? 'regex pattern' : 'Value'}
                        style={{ width: 220, flex: '1 1 200px' }}
                        value={rule.value}
                        onChange={(e) => setRules((prev) => prev.map((r) => r.id === rule.id ? { ...r, value: e.target.value } : r))}
                      />
                    ) : null}
                    <Button
                      danger
                      onClick={() => setRules((prev) => prev.filter((r) => r.id !== rule.id))}
                      disabled={rules.length === 1}
                    >
                      Remove
                    </Button>
                  </div>
                );
              })}
              <Button onClick={() => setRules((prev) => [...prev, { id: makeRuleId(), field: '', op: 'eq', value: '' }])}>
                Add Rule
              </Button>
            </Space>
          </Form.Item>
          <Divider />
          <Form.Item label="Conditions JSON (preview)">
            <Input.TextArea
              rows={5}
              value={formatContent({ logic: conditionLogic, rules: rules.map((r) => ({ field: r.field, op: r.op, value: r.value })) })}
              readOnly
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

