import React, { useState } from 'react';
import { Alert, Button, Form, Input, Typography, message } from 'antd';
import { LockOutlined, MailOutlined, UserOutlined } from '@ant-design/icons';
import { register } from 'services/accounts';

interface Props {
  onRegisterSuccess: () => void;
}

const RegisterForm: React.FC<Props> = ({ onRegisterSuccess }) => {
  const [loading, setLoading] = useState(false);

  const toErrorText = (value: unknown): string | null => {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (Array.isArray(value)) {
      for (const item of value) {
        const parsed = toErrorText(item);
        if (parsed) return parsed;
      }
      return null;
    }
    if (value && typeof value === 'object') {
      for (const item of Object.values(value as Record<string, unknown>)) {
        const parsed = toErrorText(item);
        if (parsed) return parsed;
      }
    }
    return null;
  };

  const submit = async (values: {
    username: string;
    email: string;
    password: string;
    passwordConfirm: string;
  }) => {
    setLoading(true);
    try {
      const username = String(values.username || '').trim();
      const email = String(values.email || '').trim().toLowerCase();
      await register(username, email, values.password, values.passwordConfirm);
      message.success('Profile created successfully');
      onRegisterSuccess();
    } catch (e: any) {
      const data = e?.response?.data as Record<string, unknown> | undefined;
      const errorText =
        toErrorText(data) || 'Unable to create profile. Please review your details and try again.';
      message.error(errorText);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Form layout="vertical" onFinish={submit} className="login-form-panel" aria-live="polite">
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="Create a new internal profile"
        description="Use this if you are a first-time user with no existing account."
      />

      <Form.Item
        name="username"
        label={<span className="login-form-label">User Name</span>}
        rules={[
          { required: true, message: 'Please enter a username' },
          { min: 3, message: 'Username must be at least 3 characters' },
        ]}
      >
        <Input
          prefix={<UserOutlined className="login-field-icon" />}
          placeholder="Type your username"
          autoComplete="username"
        />
      </Form.Item>

      <Form.Item
        name="email"
        label={<span className="login-form-label">Email</span>}
        rules={[
          { required: true, message: 'Please enter an email' },
          { type: 'email', message: 'Please enter a valid email' },
        ]}
      >
        <Input
          prefix={<MailOutlined className="login-field-icon" />}
          placeholder="Type your email"
          autoComplete="email"
        />
      </Form.Item>

      <Form.Item
        name="password"
        label={<span className="login-form-label">Password</span>}
        rules={[{ required: true, message: 'Please enter a password' }]}
      >
        <Input.Password
          prefix={<LockOutlined className="login-field-icon" />}
          placeholder="Type your password"
          autoComplete="new-password"
        />
      </Form.Item>

      <Form.Item
        name="passwordConfirm"
        label={<span className="login-form-label">Confirm Password</span>}
        dependencies={['password']}
        rules={[
          { required: true, message: 'Please confirm your password' },
          ({ getFieldValue }) => ({
            validator(_, value) {
              if (!value || getFieldValue('password') === value) {
                return Promise.resolve();
              }
              return Promise.reject(new Error('Passwords do not match'));
            },
          }),
        ]}
      >
        <Input.Password
          prefix={<LockOutlined className="login-field-icon" />}
          placeholder="Re-type your password"
          autoComplete="new-password"
        />
      </Form.Item>

      <Button type="primary" htmlType="submit" loading={loading} block aria-busy={loading}>
        {loading ? 'Creating Profile...' : 'Create Profile'}
      </Button>

      <Typography.Text className="login-inline-note">
        After account creation, you will be signed in automatically.
      </Typography.Text>
    </Form>
  );
};

export default RegisterForm;
