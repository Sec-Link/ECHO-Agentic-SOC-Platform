import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Divider, Form, Input, InputNumber, Select, Space, Switch, Tabs, Tag, Typography } from 'antd';
import { CodeOutlined, DeleteOutlined, FormOutlined, PlusOutlined } from '@ant-design/icons';

const { Text, Title } = Typography;
const { TextArea } = Input;

type RequestEntry = {
  key: string;
  value?: string;
  sensitive: boolean;
  configured?: boolean;
};

type Props = {
  config: Record<string, any>;
  configKey?: string;
  configuredSecretFields: string[];
  onChange: (config: Record<string, any>) => void;
};

const Entries: React.FC<{
  title: string;
  value: RequestEntry[];
  targetChanged: boolean;
  onChange: (value: RequestEntry[]) => void;
}> = ({ title, value, targetChanged, onChange }) => {
  const update = (index: number, patch: Partial<RequestEntry>) => {
    onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <Space style={{ marginBottom: 8 }}>
        <Text strong>{title}</Text>
        <Button
          size="small"
          icon={<PlusOutlined />}
          onClick={() => onChange([...value, { key: '', value: '', sensitive: false }])}
        >
          Add
        </Button>
      </Space>
      <Space direction="vertical" style={{ width: '100%' }}>
        {value.map((item, index) => {
          const configured = Boolean(item.configured && !targetChanged);
          return (
            <Space key={index} align="start" style={{ width: '100%' }} wrap>
              <Input
                aria-label={`${title} key`}
                placeholder="Key"
                value={item.key}
                onChange={(event) => update(index, { key: event.target.value, configured: false })}
                style={{ width: 190 }}
              />
              {item.sensitive ? (
                <Input.Password
                  aria-label={`${title} value`}
                  autoComplete="new-password"
                  placeholder={configured ? 'Configured - leave blank to keep it' : 'Secret value'}
                  value={item.value || ''}
                  onChange={(event) => update(index, { value: event.target.value, configured: false })}
                  style={{ width: 280 }}
                />
              ) : (
                <Input
                  aria-label={`${title} value`}
                  placeholder="Value"
                  value={item.value || ''}
                  onChange={(event) => update(index, { value: event.target.value })}
                  style={{ width: 280 }}
                />
              )}
              <Space>
                <Switch
                  checked={item.sensitive}
                  onChange={(checked) => update(index, { sensitive: checked, value: '', configured: false })}
                />
                <Text>Sensitive</Text>
                {configured && <Tag color="green">Configured</Tag>}
              </Space>
              <Button
                danger
                type="text"
                aria-label={`Delete ${title} entry`}
                icon={<DeleteOutlined />}
                onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}
              />
            </Space>
          );
        })}
      </Space>
    </div>
  );
};

const ApiCallConfigBuilder: React.FC<Props> = ({ config, configKey, configuredSecretFields, onChange }) => {
  const [mode, setMode] = useState<'form' | 'json'>('form');
  const [jsonValue, setJsonValue] = useState('');
  const [jsonError, setJsonError] = useState('');
  const initial = useRef({ key: '', url: '' });
  const method = String(config.method || 'GET').toUpperCase();
  const authType = String(config.auth_type || 'none').toLowerCase();
  const targetChanged = initial.current.url !== String(config.url || '').trim().replace(/\/+$/, '');
  const hasConfiguredSecrets = configuredSecretFields.length > 0
    || [...(config.headers || []), ...(config.query_params || [])].some((item) => item?.configured);
  const authorizationConflict = authType !== 'none'
    && (config.headers || []).some((item: RequestEntry) => item.key.trim().toLowerCase() === 'authorization');

  useEffect(() => {
    const key = configKey || '';
    if (initial.current.key !== key) {
      initial.current = { key, url: String(config.url || '').trim().replace(/\/+$/, '') };
    }
    setJsonValue(JSON.stringify(config || {}, null, 2));
  }, [config, configKey]);

  const update = (patch: Record<string, any>) => onChange({ ...config, ...patch });

  const updateAuthType = (value: string) => {
    update(value === 'none'
      ? { auth_type: value, auth_username: '', auth_secret: null }
      : { auth_type: value });
  };

  const handleJson = (value: string) => {
    setJsonValue(value);
    try {
      const parsed = JSON.parse(value);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || value.includes('enc:v1:')) {
        throw new Error();
      }
      setJsonError('');
      onChange(parsed);
    } catch {
      setJsonError('Enter a valid JSON object without encrypted values.');
    }
  };

  let bodyError = '';
  if (method !== 'GET' && config.body_template) {
    try { JSON.parse(config.body_template); } catch { bodyError = 'Body must be valid JSON.'; }
  }

  return (
    <div>
      <Tabs
        activeKey={mode}
        onChange={(key) => setMode(key as 'form' | 'json')}
        items={[
          { key: 'form', label: <span><FormOutlined /> Visual Editor</span> },
          { key: 'json', label: <span><CodeOutlined /> JSON</span> },
        ]}
      />
      {mode === 'json' ? (
        <div>
          <TextArea rows={18} value={jsonValue} onChange={(event) => handleJson(event.target.value)} style={{ fontFamily: 'monospace' }} />
          {jsonError && <Text type="danger">{jsonError}</Text>}
        </div>
      ) : (
        <Form layout="vertical">
          <Card size="small" style={{ marginBottom: 16 }}>
            <Title level={5} style={{ margin: 0 }}>API Call</Title>
            <Text type="secondary">Call an HTTP API with a fixed target and configurable request data.</Text>
          </Card>
          {targetChanged && hasConfiguredSecrets && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="Secret re-entry required"
              description="The URL changed. Re-enter every configured authentication, sensitive header, and sensitive query value."
            />
          )}
          {authorizationConflict && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              message="Remove the Authorization header or select no built-in authentication."
            />
          )}
          <Form.Item
            label="URL"
            required
            extra="The hostname or IP address must be present in the administrator-managed API target allowlist."
          >
            <Input value={config.url || ''} onChange={(event) => update({ url: event.target.value })} placeholder="https://api.example.com/v1/resource" />
          </Form.Item>
          <Space size="large" wrap style={{ width: '100%' }}>
            <Form.Item label="Method">
              <Select
                value={method}
                style={{ width: 140 }}
                options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) => ({ value, label: value }))}
                onChange={(value) => update(value === 'GET' ? { method: value, body_template: '' } : { method: value })}
              />
            </Form.Item>
            <Form.Item label="Timeout (seconds)">
              <InputNumber min={1} max={120} value={config.timeout ?? 30} onChange={(value) => update({ timeout: value })} />
            </Form.Item>
            <Form.Item label="Verify TLS">
              <Switch checked={config.verify_tls !== false} onChange={(value) => update({ verify_tls: value })} />
            </Form.Item>
          </Space>
          <Divider orientation="left">Authentication</Divider>
          <Form.Item label="Authentication type">
            <Select
              value={authType}
              options={[
                { value: 'none', label: 'None' },
                { value: 'bearer', label: 'Bearer token' },
                { value: 'basic', label: 'Basic authentication' },
              ]}
              onChange={updateAuthType}
            />
          </Form.Item>
          {authType === 'basic' && (
            <Form.Item label="Username" required>
              <Input value={config.auth_username || ''} onChange={(event) => update({ auth_username: event.target.value })} />
            </Form.Item>
          )}
          {authType !== 'none' && (
            <Form.Item
              label={<Space>Authentication secret {configuredSecretFields.includes('auth_secret') && !targetChanged && <Tag color="green">Configured</Tag>}</Space>}
              required
            >
              <Input.Password
                autoComplete="new-password"
                value={config.auth_secret || ''}
                placeholder={configuredSecretFields.includes('auth_secret') && !targetChanged ? 'Configured - leave blank to keep it' : 'Enter secret'}
                onChange={(event) => update({ auth_secret: event.target.value })}
              />
            </Form.Item>
          )}
          <Divider orientation="left">Request data</Divider>
          <Entries title="Headers" value={config.headers || []} targetChanged={targetChanged} onChange={(headers) => update({ headers })} />
          <Entries title="Query parameters" value={config.query_params || []} targetChanged={targetChanged} onChange={(query_params) => update({ query_params })} />
          {method !== 'GET' && (
            <Form.Item label="JSON body" validateStatus={bodyError ? 'error' : ''} help={bodyError}>
              <TextArea
                rows={8}
                style={{ fontFamily: 'monospace' }}
                value={config.body_template || ''}
                onChange={(event) => update({ body_template: event.target.value })}
                placeholder={'{"ip": "{{trigger_data.source_ip}}"}'}
              />
            </Form.Item>
          )}
          <Alert type="info" showIcon message={<span>Values support <code>{'{{variable.path}}'}</code> placeholders. Hostnames cannot be dynamic.</span>} />
        </Form>
      )}
    </div>
  );
};

export default ApiCallConfigBuilder;
