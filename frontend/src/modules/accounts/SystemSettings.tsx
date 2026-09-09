'use client';

import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Input, Space, Typography, message } from 'antd';
import { getSystemSettings, updateSystemSettings } from 'services/accounts';

const SystemSettings: React.FC = () => {
  const [allowlist, setAllowlist] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getSystemSettings()
      .then((result) => setAllowlist((result?.workflow_http_allowlist || []).join('\n')))
      .catch((error) => message.error(error?.response?.data?.detail || 'Failed to load system settings'))
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const entries = allowlist.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      const result = await updateSystemSettings({ workflow_http_allowlist: entries });
      setAllowlist((result?.workflow_http_allowlist || []).join('\n'));
      message.success('API target allowlist saved.');
    } catch (error: any) {
      const detail = error?.response?.data?.workflow_http_allowlist;
      message.error(Array.isArray(detail) ? detail.join(' ') : detail || 'Failed to save system settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="System Settings" loading={loading}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <div>
          <Typography.Title level={5}>Allowed API Targets</Typography.Title>
          <Typography.Text type="secondary">
            Enter one exact hostname, IP address, or CIDR per line. All other public and private targets are denied.
          </Typography.Text>
        </div>
        <Alert
          type="warning"
          showIcon
          message="Loopback, link-local, unspecified, multicast, and cloud metadata targets are always blocked."
        />
        <Input.TextArea
          rows={10}
          value={allowlist}
          onChange={(event) => setAllowlist(event.target.value)}
          placeholder={'firewall.internal\n10.20.0.0/16\n192.168.1.10'}
          style={{ fontFamily: 'monospace' }}
          aria-label="API target allowlist"
        />
        <Button type="primary" loading={saving} onClick={save}>Save</Button>
      </Space>
    </Card>
  );
};

export default SystemSettings;
