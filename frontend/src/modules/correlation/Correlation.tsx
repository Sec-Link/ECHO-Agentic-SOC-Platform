import React, { useEffect, useMemo, useState } from 'react'
import { Card, Form, InputNumber, Select, Switch, Button, Space, message, Table, Tag, Divider, Typography, Input, Tabs } from 'antd'
import { Line } from '@ant-design/plots'
import { getCorrelationPolicy, saveCorrelationPolicy, getCorrelationEvents } from 'services/correlation'
import { listIntegrations, integrationsPreviewEsMapping } from 'services/integrations'

const { Title, Text } = Typography

const RANGE_OPTIONS: Array<{ key: string; label: string; minutes: number; bucket: string }> = [
  { key: '1h', label: 'Last 1 hour', minutes: 60, bucket: '5m' },
  { key: '6h', label: 'Last 6 hours', minutes: 360, bucket: '15m' },
  { key: '24h', label: 'Last 24 hours', minutes: 1440, bucket: '1h' },
  { key: '7d', label: 'Last 7 days', minutes: 10080, bucket: '3h' },
]

const defaultPolicy = {
  enabled: false,
  window_minutes: 30,
  match_keys: [],
  rules_expression: {
    window_minutes: 30,
    order_by: [],
    es_source: { integration_id: null, index: null },
  },
}

function exportCsv(rows: any[], filename: string) {
  const header = ['ticket_id', 'alert_count', 'last_alert_time', 'top_threat_object', 'top_rule']
  const lines = [header.join(',')]
  rows.forEach(r => {
    lines.push(header.map(k => (r[k] !== undefined ? JSON.stringify(String(r[k])) : '')).join(','))
  })
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  link.click()
}

type Props = {
  onNavigate?: (path: string) => void
}

const Correlation: React.FC<Props> = ({ onNavigate }) => {
  const [form] = Form.useForm()
  const [loadingPolicy, setLoadingPolicy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [policy, setPolicy] = useState<any>(defaultPolicy)
  const [rangeKey, setRangeKey] = useState<string>('6h')
  const [bucket, setBucket] = useState<string>('15m')
  const [eventsLoading, setEventsLoading] = useState(false)
  const [series, setSeries] = useState<any[]>([])
  const [tableData, setTableData] = useState<any[]>([])
  const [integrations, setIntegrations] = useState<any[]>([])
  const [esFields, setEsFields] = useState<string[]>([])
  const [loadingFields, setLoadingFields] = useState(false)

  useEffect(() => {
    setLoadingPolicy(true)
    getCorrelationPolicy()
      .then((res) => {
        const rules = res?.rules_expression || {}
        const orderBy = rules.order_by || res.match_keys || []
        const windowMinutes = rules.window_minutes ?? res.window_minutes ?? 30
        const esSource = rules.es_source || {}
        setPolicy(res)
        form.setFieldsValue({
          enabled: res.enabled,
          window_minutes: windowMinutes,
          order_by: orderBy,
          alerts_id: esSource.integration_id ?? null,
          es_index: esSource.index ?? null,
        })
      })
      .catch(() => {
        form.setFieldsValue({
          enabled: defaultPolicy.enabled,
          window_minutes: defaultPolicy.window_minutes,
          order_by: defaultPolicy.rules_expression.order_by,
          alerts_id: defaultPolicy.rules_expression.es_source.integration_id,
          es_index: defaultPolicy.rules_expression.es_source.index,
        })
      })
      .finally(() => setLoadingPolicy(false))
  }, [form])

  useEffect(() => {
    const load = async () => {
      try {
        const res = await listIntegrations()
        setIntegrations(Array.isArray(res) ? res : (res?.results ?? []))
      } catch {
        setIntegrations([])
      }
    }
    load()
  }, [])

  const loadEsFields = async () => {
    const values = form.getFieldsValue(['alerts_id', 'es_index'])
    if (!values.alerts_id || !values.es_index) {
      message.error('Select ES integration and index first')
      return
    }
    setLoadingFields(true)
    try {
      const res = await integrationsPreviewEsMapping({
        alerts: values.alerts_id,
        index: values.es_index,
        size: 1,
      })
      const cols = Array.isArray(res?.columns) ? res.columns : []
      const fields = cols.map((c: any) => c.orig_name).filter(Boolean)
      setEsFields(Array.from(new Set(fields)))
      if (!fields.length) message.warning('No fields returned from ES mapping')
    } catch (e: any) {
      message.error(e?.message || 'Failed to load ES fields')
      setEsFields([])
    } finally {
      setLoadingFields(false)
    }
  }

  const refreshEvents = async (options?: { seed?: boolean }) => {
    setEventsLoading(true)
    const opt = RANGE_OPTIONS.find(o => o.key === rangeKey) || RANGE_OPTIONS[1]
    const to = new Date()
    const from = new Date(to.getTime() - opt.minutes * 60 * 1000)
    const effectiveBucket = bucket || opt.bucket
    if (!bucket) setBucket(opt.bucket)
    try {
      const res = await getCorrelationEvents({ from: from.toISOString(), to: to.toISOString(), bucket: effectiveBucket, seed: options?.seed })
      setSeries(res.series || [])
      setTableData(res.table || [])
      if (options?.seed && res?.seeded) {
        const created = res.seeded.created ?? 0
        const tickets = res.seeded.tickets ?? 0
        message.success(`Seeded ${created} correlation events across ${tickets} tickets`)
      }
    } catch (e) {
      message.error('Failed to load correlation data')
      setSeries([])
      setTableData([])
    } finally {
      setEventsLoading(false)
    }
  }

  useEffect(() => {
    refreshEvents().catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeKey])

  const onSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const orderBy = values.order_by || []
      const payload = {
        enabled: values.enabled,
        window_minutes: values.window_minutes,
        match_keys: orderBy,
        rules_expression: {
          window_minutes: values.window_minutes,
          order_by: orderBy,
          es_source: {
            integration_id: values.alerts_id || null,
            index: values.es_index || null,
          },
        },
      }
      const res = await saveCorrelationPolicy(payload)
      setPolicy(res)
      message.success('Policy saved')
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const lineConfig = useMemo(() => ({
    data: series,
    xField: 'time',
    yField: 'count',
    smooth: true,
    height: 260,
    xAxis: { type: 'time' },
    slider: { start: 0, end: 1 },
    tooltip: { showMarkers: true },
  }), [series])

  const columns = [
    { title: 'Ticket', dataIndex: 'ticket_id', key: 'ticket_id', render: (v: string) => (
      v ? <Button type="link" onClick={() => onNavigate && onNavigate(`/tickets/${encodeURIComponent(v)}`)}>{v}</Button> : '-'
    ) },
    { title: 'Alerts', dataIndex: 'alert_count', key: 'alert_count' },
    { title: 'Last Alert', dataIndex: 'last_alert_time', key: 'last_alert_time', render: (v: string) => <Text>{v}</Text> },
    { title: 'Risk Object', dataIndex: 'top_threat_object', key: 'top_threat_object' },
    { title: 'Rule', dataIndex: 'top_rule', key: 'top_rule' },
    { title: 'Alert IDs', dataIndex: 'alert_ids', key: 'alert_ids', render: (arr: string[]) => (arr || []).map(id => <Tag key={id}>{id}</Tag>) },
  ]

  return (
    <div style={{ padding: 16 }}>
      <Title level={3}>Correlation</Title>
      <Text type="secondary">Configure correlation ordering rules and review correlated activity.</Text>
      <Divider />

      <Tabs
        defaultActiveKey="policy"
        items={[
          {
            key: 'policy',
            label: 'Correlation Policy',
            children: (
              <Card title="Policy" loading={loadingPolicy}>
                <Form form={form} layout="vertical">
                  <Form.Item label="Enabled" name="enabled" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  <Form.Item label="Window (minutes)" name="window_minutes" rules={[{ required: true, message: "Window minutes required" }]}>
                    <InputNumber min={1} max={1440} style={{ width: 200 }} />
                  </Form.Item>
                  <Form.Item name="alerts_id" label="ES Integration">
                    <Select
                      allowClear
                      placeholder="Select integration"
                      options={integrations.filter((i) => (i.type || "").toString().toLowerCase() === "elasticsearch").map((i) => ({
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
                    <Text type="secondary">{esFields.length ? `${esFields.length} fields loaded` : "No fields loaded"}</Text>
                  </Space>
                  <Form.Item label="Order By Fields" name="order_by" rules={[{ required: true, message: "Select fields" }]}>
                    <Select
                      mode="multiple"
                      showSearch
                      placeholder={esFields.length ? "Select fields" : "Load ES fields first"}
                      options={esFields.map((f) => ({ label: f, value: f }))}
                      disabled={!esFields.length}
                      style={{ width: 360 }}
                    />
                  </Form.Item>
                  <Space>
                    <Button type="primary" onClick={onSave} loading={saving}>Save</Button>
                    <Button onClick={() => form.resetFields()}>Reset</Button>
                  </Space>
                </Form>
              </Card>
            ),
          },
          {
            key: 'activity',
            label: 'Correlation Activity',
            children: (
              <Card title="Activity" extra={
                <Space>
                  <Select value={rangeKey} style={{ width: 140 }} onChange={setRangeKey} options={RANGE_OPTIONS.map(r => ({ label: r.label, value: r.key }))} />
                  <Select value={bucket} style={{ width: 110 }} onChange={setBucket} options={['5m','15m','1h','3h','6h'].map(v => ({ label: v, value: v }))} />
                  <Button onClick={() => refreshEvents()} loading={eventsLoading}>Refresh</Button>
                  <Button onClick={() => refreshEvents({ seed: true })} loading={eventsLoading}>Seed Data</Button>
                  <Button onClick={() => exportCsv(tableData, 'correlation.csv')}>Export CSV</Button>
                </Space>
              }>
                <div style={{ marginBottom: 16 }}>
                  <Line {...lineConfig} />
                </div>
                <Table
                  size="small"
                  loading={eventsLoading}
                  dataSource={tableData}
                  columns={columns}
                  rowKey={(r) => r.ticket_id + (r.last_alert_time || '')}
                />
              </Card>
            ),
          },
        ]}
      />
    </div>
  )
}

export default Correlation

