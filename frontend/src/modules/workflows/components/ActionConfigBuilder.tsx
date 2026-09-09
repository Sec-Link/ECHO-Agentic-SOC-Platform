/**
 * Visual editor for workflow action configuration.
 *
 * Field definitions come from the backend Action Registry through the
 * available-actions API. This component only maps JSON Schema primitives to
 * Ant Design controls and keeps the existing credential-protection behaviour.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Divider,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  CodeOutlined,
  FormOutlined,
  InfoCircleOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import type { ActionInfo } from 'services/workflows';
import ApiCallConfigBuilder from './ApiCallConfigBuilder';

const { TextArea } = Input;
const { Text } = Typography;

type JsonSchemaProperty = {
  type?: string | string[];
  title?: string;
  description?: string;
  enum?: unknown[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  writeOnly?: boolean;
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
  'x-sensitive'?: boolean;
};

type ActionConfigSchema = {
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
};

type FieldDef = {
  name: string;
  label: string;
  type: 'string' | 'number' | 'boolean' | 'select' | 'textarea' | 'array' | 'password' | 'keyvalue';
  required: boolean;
  options?: Array<{ value: string | number; label: string }>;
  default?: unknown;
  description?: string;
  sensitive: boolean;
  minimum?: number;
  maximum?: number;
};

interface ActionConfigBuilderProps {
  actionType: string;
  actionInfo?: ActionInfo;
  config: Record<string, any>;
  configKey?: string;
  configuredSecretFields?: string[];
  onChange: (config: Record<string, any>) => void;
}

const ACRONYMS: Record<string, string> = {
  api: 'API',
  html: 'HTML',
  id: 'ID',
  ip: 'IP',
  json: 'JSON',
  tls: 'TLS',
  uid: 'UID',
  upn: 'UPN',
  url: 'URL',
};

const formatLabel = (name: string): string => (
  name
    .split('_')
    .map((part) => ACRONYMS[part.toLowerCase()] || `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
);

const formatOptionLabel = (value: unknown): string => {
  const raw = String(value);
  if (raw && raw === raw.toUpperCase()) return raw;
  return formatLabel(raw.replace(/-/g, '_'));
};

const isMultilineField = (name: string): boolean => (
  /(^|_)(body|comment|description|message|notes?|records?|template)$/.test(name)
);

const resolveFieldType = (
  name: string,
  property: JsonSchemaProperty,
  sensitive: boolean,
): FieldDef['type'] => {
  if (Array.isArray(property.enum)) return 'select';

  const propertyType = Array.isArray(property.type)
    ? property.type.find((item) => item !== 'null')
    : property.type;

  if (propertyType === 'object') return 'keyvalue';
  if (propertyType === 'array') return 'array';
  if (propertyType === 'boolean') return 'boolean';
  if (propertyType === 'integer' || propertyType === 'number') return 'number';
  if (sensitive) return 'password';
  if (isMultilineField(name)) return 'textarea';
  return 'string';
};

const fieldsFromSchema = (schema?: ActionConfigSchema): FieldDef[] => {
  const required = new Set(schema?.required || []);
  return Object.entries(schema?.properties || {}).map(([name, property]) => {
    const sensitive = Boolean(property.writeOnly || property['x-sensitive']);
    return {
      name,
      label: property.title || formatLabel(name),
      type: resolveFieldType(name, property, sensitive),
      required: required.has(name),
      options: property.enum?.map((value) => ({
        value: typeof value === 'number' ? value : String(value),
        label: formatOptionLabel(value),
      })),
      default: property.default,
      description: property.description,
      sensitive,
      minimum: property.minimum,
      maximum: property.maximum,
    };
  });
};

// Provider-specific visibility is presentation behaviour that is not encoded
// in the current backend JSON Schema. Field definitions still come entirely
// from the registry response.
const isFieldVisible = (actionType: string, fieldName: string, provider: string): boolean => {
  if (actionType !== 'block_ip' && actionType !== 'release_ip') return true;

  const normalizedProvider = String(provider || 'generic').toLowerCase();
  if (normalizedProvider === 'opnsense') {
    return fieldName !== 'duration_hours';
  }

  return !['api_secret', 'alias_name', 'verify_tls', 'kill_states'].includes(fieldName);
};

const ArrayInput: React.FC<{
  value?: string[];
  onChange?: (value: string[]) => void;
}> = ({ value = [], onChange }) => {
  const [inputValue, setInputValue] = useState('');
  const items = Array.isArray(value) ? value : [];

  const handleAdd = () => {
    const nextValue = inputValue.trim();
    if (nextValue && !items.includes(nextValue)) {
      onChange?.([...items, nextValue]);
      setInputValue('');
    }
  };

  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Input
          value={inputValue}
          onChange={(event) => setInputValue(event.target.value)}
          onPressEnter={handleAdd}
          style={{ width: 220 }}
        />
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleAdd}>
          Add
        </Button>
      </Space>
      <div>
        {items.map((item, index) => (
          <Tag
            key={`${item}-${index}`}
            closable
            onClose={() => onChange?.(items.filter((_, itemIndex) => itemIndex !== index))}
            style={{ marginBottom: 4 }}
          >
            {item}
          </Tag>
        ))}
      </div>
    </div>
  );
};

const KeyValueInput: React.FC<{
  value?: Record<string, string>;
  onChange?: (value: Record<string, string>) => void;
}> = ({ value = {}, onChange }) => {
  const [keyInput, setKeyInput] = useState('');
  const [valueInput, setValueInput] = useState('');
  const entries = Object.entries(value || {});

  const handleAdd = () => {
    const key = keyInput.trim();
    if (!key) return;
    onChange?.({ ...value, [key]: valueInput });
    setKeyInput('');
    setValueInput('');
  };

  const handleRemove = (key: string) => {
    const next = { ...value };
    delete next[key];
    onChange?.(next);
  };

  return (
    <div>
      <Space style={{ marginBottom: 8, width: '100%' }} wrap>
        <Input
          placeholder="Key"
          value={keyInput}
          onChange={(event) => setKeyInput(event.target.value)}
          style={{ width: 180 }}
        />
        <Input
          placeholder="Value"
          value={valueInput}
          onChange={(event) => setValueInput(event.target.value)}
          style={{ width: 220 }}
        />
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleAdd}>
          Add
        </Button>
      </Space>
      <div>
        {entries.map(([key, itemValue]) => (
          <Tag
            key={key}
            closable
            onClose={() => handleRemove(key)}
            style={{ marginBottom: 4 }}
          >
            {key}: {itemValue}
          </Tag>
        ))}
      </div>
    </div>
  );
};

const ActionConfigBuilder: React.FC<ActionConfigBuilderProps> = ({
  actionType,
  actionInfo,
  config,
  configKey,
  configuredSecretFields = [],
  onChange,
}) => {
  const [mode, setMode] = useState<'form' | 'json'>('form');
  const [jsonValue, setJsonValue] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [protectedTargetChanged, setProtectedTargetChanged] = useState(false);
  const [aliasChanged, setAliasChanged] = useState(false);
  const [form] = Form.useForm();
  const loadedConfigKey = useRef<string | null>(null);
  const initialTargets = useRef<Record<string, any>>({});
  const provider = Form.useWatch('provider', form) || config?.provider || 'generic';

  const schema = actionInfo?.config_schema as ActionConfigSchema | undefined;
  const fields = fieldsFromSchema(schema);
  const configuredSecretSet = new Set(configuredSecretFields);
  const targetCredentialFields = fields
    .filter((field) => field.sensitive)
    .map((field) => field.name);
  const isConfiguredForCurrentTarget = (fieldName: string) => (
    configuredSecretSet.has(fieldName)
    && !(protectedTargetChanged && targetCredentialFields.includes(fieldName))
  );

  const normalizeTargetValue = (value: any) => String(value || '').trim().replace(/\/+$/, '');

  useEffect(() => {
    const nextConfigKey = `${actionType}:${configKey || ''}`;
    if (loadedConfigKey.current === nextConfigKey) return;
    loadedConfigKey.current = nextConfigKey;
    initialTargets.current = {
      provider: String(config?.provider || 'generic').toLowerCase(),
      api_url: normalizeTargetValue(config?.api_url),
      url: normalizeTargetValue(config?.url),
      alias_name: String(config?.alias_name || 'ARGUS_BLOCKLIST').trim(),
    };
    setProtectedTargetChanged(false);
    setAliasChanged(false);

    const safeConfig = Object.fromEntries(
      Object.entries(config || {}).filter(([name, value]) => (
        !configuredSecretFields.includes(name)
        && !(typeof value === 'string' && value.startsWith('enc:v1:'))
      )),
    );
    form.resetFields();
    form.setFieldsValue(safeConfig);
    setJsonValue(JSON.stringify(safeConfig, null, 2));
  }, [actionType, config, configKey, configuredSecretFields, form]);

  const applyTargetProtection = (inputValues: Record<string, any>) => {
    const values = { ...inputValues };
    const initial = initialTargets.current;
    const apiUrlChanged = normalizeTargetValue(values.api_url) !== initial.api_url;
    const providerChanged = (
      String(values.provider || 'generic').toLowerCase() !== initial.provider
    );
    const isOPNsenseAction = actionType === 'block_ip' || actionType === 'release_ip';
    const protectedChanged = configuredSecretFields.length > 0
      && (apiUrlChanged || (isOPNsenseAction && providerChanged));
    const nextAliasChanged = (
      isOPNsenseAction
      && configuredSecretFields.some((field) => field === 'api_key' || field === 'api_secret')
      && String(values.alias_name || 'ARGUS_BLOCKLIST').trim() !== initial.alias_name
    );
    setProtectedTargetChanged(protectedChanged);
    setAliasChanged(nextAliasChanged);

    if (isOPNsenseAction && values.provider === 'generic') {
      values.api_secret = null;
    }
    return values;
  };

  const handleFormChange = () => {
    const values = applyTargetProtection(form.getFieldsValue(true));
    onChange(values);
    setJsonValue(JSON.stringify(values, null, 2));
  };

  const handleJsonChange = (value: string) => {
    setJsonValue(value);
    try {
      const parsed = JSON.parse(value);
      if (Object.values(parsed).some((item) => (
        typeof item === 'string' && item.startsWith('enc:v1:')
      ))) {
        setJsonError('Encrypted values cannot be viewed or edited here');
        return;
      }
      const protectedValues = applyTargetProtection(parsed);
      setJsonError(null);
      onChange(protectedValues);
      form.setFieldsValue(protectedValues);
    } catch {
      setJsonError('Invalid JSON format');
    }
  };

  const renderField = (field: FieldDef) => {
    switch (field.type) {
      case 'password':
        return (
          <Input.Password
            autoComplete="new-password"
            placeholder={
              isConfiguredForCurrentTarget(field.name)
                ? 'Configured — leave blank to keep it'
                : `Enter ${field.label}`
            }
          />
        );
      case 'number':
        return (
          <InputNumber
            style={{ width: '100%' }}
            min={field.minimum}
            max={field.maximum}
          />
        );
      case 'boolean':
        return <Switch />;
      case 'select':
        return <Select options={field.options} placeholder="Select…" allowClear />;
      case 'textarea':
        return <TextArea rows={4} style={{ fontFamily: 'monospace', fontSize: 12 }} />;
      case 'array':
        return <ArrayInput />;
      case 'keyvalue':
        return <KeyValueInput />;
      default:
        return <Input />;
    }
  };

  if (actionType === 'api_call') {
    return (
      <ApiCallConfigBuilder
        config={config}
        configKey={configKey}
        configuredSecretFields={configuredSecretFields}
        onChange={onChange}
      />
    );
  }

  if (!schema?.properties) {
    return (
      <div>
        <Alert
          message="Custom Action"
          description="No configuration schema is available for this action type. Configure it as JSON below."
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        <TextArea
          rows={10}
          style={{ fontFamily: 'monospace' }}
          value={jsonValue}
          onChange={(event) => handleJsonChange(event.target.value)}
        />
        {jsonError && <Text type="danger">{jsonError}</Text>}
      </div>
    );
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
        style={{ marginBottom: 16 }}
      />

      {mode === 'form' ? (
        <Form
          form={form}
          layout="vertical"
          onValuesChange={handleFormChange}
          initialValues={config}
        >
          <Card size="small" style={{ marginBottom: 16, background: '#f5f5f5' }}>
            <Text strong>{actionInfo?.name || formatLabel(actionType)}</Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>{actionInfo?.description}</Text>
          </Card>

          {protectedTargetChanged && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="Credential re-entry required"
              description="The Provider or API URL changed. Re-enter the API Key and, for OPNsense, the API Secret before saving."
            />
          )}

          {aliasChanged && !protectedTargetChanged && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="Existing OPNsense credentials will be reused"
              description="Only the Alias Name changed. The saved API Key and API Secret will be securely rebound to the new alias."
            />
          )}

          {fields
            .filter((field) => isFieldVisible(actionType, field.name, provider))
            .map((field) => {
              const required = field.required || (
                (actionType === 'block_ip' || actionType === 'release_ip')
                && String(provider).toLowerCase() === 'opnsense'
                && field.name === 'api_secret'
              );
              return (
                <Form.Item
                  key={field.name}
                  name={field.name}
                  label={
                    <Space>
                      {field.label}
                      {required && <Text type="danger">*</Text>}
                      {isConfiguredForCurrentTarget(field.name) && (
                        <Tag color="green">Configured</Tag>
                      )}
                      {field.description && (
                        <Tooltip title={field.description}>
                          <InfoCircleOutlined style={{ color: '#999' }} />
                        </Tooltip>
                      )}
                    </Space>
                  }
                  rules={
                    required && !isConfiguredForCurrentTarget(field.name)
                      ? [{ required: true, message: `${field.label} is required` }]
                      : []
                  }
                  valuePropName={field.type === 'boolean' ? 'checked' : 'value'}
                  initialValue={field.default}
                  help={
                    isConfiguredForCurrentTarget(field.name)
                      ? 'Already configured. Leave empty to keep the existing value.'
                      : undefined
                  }
                >
                  {renderField(field)}
                </Form.Item>
              );
            })}

          <Divider />
          <Alert
            message="Variable Syntax"
            description={
              <div>
                <Text>
                  Use <code>{'{{variable.path}}'}</code> to insert dynamic values from the
                  triggering event:
                </Text>
                <ul style={{ marginBottom: 0, paddingLeft: 20, fontSize: 12 }}>
                  <li><code>{'{{trigger_data.severity}}'}</code> – Alert / ticket severity</li>
                  <li><code>{'{{trigger_data.source_ip}}'}</code> – Source IP address</li>
                  <li><code>{'{{trigger_data.username}}'}</code> – Associated username</li>
                  <li><code>{'{{trigger_data.file_hash}}'}</code> – File hash</li>
                  <li><code>{'{{trigger_data.alert_name}}'}</code> – Alert name</li>
                  <li><code>{'{{trigger_data.ticket_number}}'}</code> – Ticket number</li>
                </ul>
                <Text style={{ fontSize: 11 }}>
                  See the <strong>Variable Reference Guide</strong> for the full list.
                </Text>
              </div>
            }
            type="info"
            showIcon
          />
        </Form>
      ) : (
        <div>
          <TextArea
            rows={15}
            style={{ fontFamily: 'monospace' }}
            value={jsonValue}
            onChange={(event) => handleJsonChange(event.target.value)}
          />
          {jsonError && (
            <Text type="danger" style={{ display: 'block', marginTop: 8 }}>
              {jsonError}
            </Text>
          )}
        </div>
      )}
    </div>
  );
};

export default ActionConfigBuilder;
