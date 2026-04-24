import React, { useState } from 'react';
import { Form, Input, Button, message } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { login } from 'services/accounts';

interface Props {
  onLoginSuccess: () => void;
}

const InternalLoginForm: React.FC<Props> = ({ onLoginSuccess }) => {
  const [loading, setLoading] = useState(false);

  const submit = async (values: { identifier: string; password: string }) => {
    setLoading(true);
    try {
      const identifier = (values.identifier || '').trim();
      await login(identifier, values.password);
      message.success(`Login succeeded: ${identifier}`);
      onLoginSuccess();
    } catch (e: any) {
      message.error(e?.response?.data?.error || e?.response?.data?.detail || 'Internal login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Form layout="vertical" onFinish={submit} className="login-form-panel" aria-live="polite">
      <Form.Item
        name="identifier"
        label={<span style={{ color: '#9ab8e6' }}>Email / Username</span>}
        rules={[{ required: true, message: 'Please enter email or username' }]}
      >
        <Input
          prefix={<UserOutlined style={{ color: 'rgba(216, 232, 255, 0.72)' }} />}
          placeholder="your account"
          autoComplete="username"
        />
      </Form.Item>

      <Form.Item
        name="password"
        label={<span style={{ color: '#9ab8e6' }}>Password</span>}
        rules={[{ required: true, message: 'Please enter password' }]}
      >
        <Input.Password
          prefix={<LockOutlined style={{ color: 'rgba(216, 232, 255, 0.72)' }} />}
          placeholder="password"
          autoComplete="current-password"
        />
      </Form.Item>

      <Button type="primary" htmlType="submit" loading={loading} block aria-busy={loading}>
        {loading ? 'Logging in...' : 'Login'}
      </Button>
    </Form>
  );
};

export default InternalLoginForm;

