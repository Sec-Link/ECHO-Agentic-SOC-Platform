import React, { useEffect, useMemo, useState } from 'react';
import { Card, Space, Typography } from 'antd';
import LoginSwitcher, { LoginMode } from './LoginSwitcher';
import InternalLoginForm from './InternalLoginForm';
import ExternalOtpForm from './ExternalOtpForm';

interface Props {
  onLogin: () => void;
}

const STORAGE_KEY = 'siem_login_mode';

const LoginForm: React.FC<Props> = ({ onLogin }) => {
  const [mode, setMode] = useState<LoginMode>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved === 'internal' || saved === 'externalOtp') return saved;
    } catch {}
    return 'internal';
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {}
  }, [mode]);

  const panelTitle = useMemo(() => {
    return mode === 'internal' ? 'Internal Account Login' : 'External OTP Login';
  }, [mode]);

  return (
    <div className="login-cyber-wrap">
      <div className="login-cyber-bg-grid" />
      <div className="login-cyber-glow login-cyber-glow-a" />
      <div className="login-cyber-glow login-cyber-glow-b" />
      <Card className="login-cyber-card" role="region" aria-label="Authentication panel">
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <Typography.Title level={3} style={{ textAlign: 'center', margin: 0, color: '#d8e8ff' }}>
            Welcome
          </Typography.Title>
          <Typography.Text style={{ textAlign: 'center', color: '#9ab8e6' }}>{panelTitle}</Typography.Text>
          <LoginSwitcher mode={mode} onChange={setMode} />

          <div className="login-mode-panel-wrap" key={mode}>
            {mode === 'internal' ? (
              <InternalLoginForm onLoginSuccess={onLogin} />
            ) : (
              <ExternalOtpForm onLoginSuccess={onLogin} />
            )}
          </div>
        </Space>
      </Card>
    </div>
  );
};

export default LoginForm;

