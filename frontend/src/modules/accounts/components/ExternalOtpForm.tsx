import React, { useEffect, useMemo, useState } from 'react';
import { Form, Input, Button, message, Space, Typography } from 'antd';
import { MailOutlined, SafetyCertificateOutlined, ReloadOutlined } from '@ant-design/icons';
import { registerEmail, requestOtp, verifyOtp } from 'services/accounts';

interface Props {
  onLoginSuccess: () => void;
}

const RESEND_SECONDS = 60;

const ExternalOtpForm: React.FC<Props> = ({ onLoginSuccess }) => {
  const [email, setEmail] = useState('');
  const [step, setStep] = useState<'request' | 'verify'>('request');
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);

  useEffect(() => {
    if (secondsLeft <= 0) return;
    const timer = window.setInterval(() => {
      setSecondsLeft((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [secondsLeft]);

  const normalizedEmail = useMemo(() => (email || '').trim().toLowerCase(), [email]);
  const canResend = step === 'verify' && secondsLeft <= 0 && !sending;

  const sendOtp = async () => {
    if (!normalizedEmail) {
      message.warning('Please enter email');
      return;
    }
    setSending(true);
    try {
      await requestOtp(normalizedEmail);
      setStep('verify');
      setSecondsLeft(RESEND_SECONDS);
      message.success('If eligible, an OTP has been sent to your email.');
    } catch (e: any) {
      const retryAfter = Number(e?.response?.data?.retry_after_seconds || 0);
      if (e?.response?.status === 429 && retryAfter > 0) {
        setStep('verify');
        setSecondsLeft(retryAfter);
        message.warning(e?.response?.data?.message || `Too many requests. Retry in ${retryAfter}s.`);
      } else {
        message.error(e?.response?.data?.detail || e?.response?.data?.message || 'Failed to send OTP');
      }
    } finally {
      setSending(false);
    }
  };

  const verify = async (values: { otp: string }) => {
    setVerifying(true);
    try {
      await verifyOtp(normalizedEmail, String(values.otp || '').trim());
      message.success(`Login succeeded: ${normalizedEmail}`);
      onLoginSuccess();
    } catch (e: any) {
      message.error(e?.response?.data?.error || e?.response?.data?.detail || 'OTP verification failed');
    } finally {
      setVerifying(false);
    }
  };

  const submitRegistration = async () => {
    if (!normalizedEmail) {
      message.warning('Please enter email');
      return;
    }
    setRegistering(true);
    try {
      await registerEmail(normalizedEmail);
      message.success('Registration submitted and pending admin approval.');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Registration submit failed');
    } finally {
      setRegistering(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Form layout="vertical" className="login-form-panel" onFinish={verify} aria-live="polite">
        <Form.Item label={<span style={{ color: '#9ab8e6' }}>Email</span>} required>
          <Input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={step === 'verify'}
            prefix={<MailOutlined style={{ color: 'rgba(216, 232, 255, 0.72)' }} />}
            placeholder="you@example.com"
            autoComplete="email"
          />
        </Form.Item>

        {step === 'verify' ? (
          <Form.Item
            name="otp"
            label={<span style={{ color: '#9ab8e6' }}>OTP Code</span>}
            rules={[
              { required: true, message: 'Please enter OTP' },
              { pattern: /^\d{6}$/, message: 'OTP must be a 6-digit number' },
            ]}
          >
            <Input
              maxLength={6}
              prefix={<SafetyCertificateOutlined style={{ color: 'rgba(216, 232, 255, 0.72)' }} />}
              placeholder="123456"
            />
          </Form.Item>
        ) : null}

        {step === 'request' ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Button type="primary" loading={sending} onClick={sendOtp} block aria-busy={sending}>
              {sending ? 'Sending OTP...' : 'Send OTP'}
            </Button>
            <Button loading={registering} onClick={submitRegistration} block aria-busy={registering}>
              Register Email
            </Button>
          </Space>
        ) : (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Button type="primary" htmlType="submit" loading={verifying} block aria-busy={verifying}>
              {verifying ? 'Verifying...' : 'Verify & Login'}
            </Button>
            <Button
              icon={<ReloadOutlined />}
              disabled={!canResend}
              loading={sending}
              onClick={sendOtp}
              block
              className="otp-resend-btn"
            >
              {canResend ? 'Resend OTP' : `Resend in ${secondsLeft}s`}
            </Button>
            <Button
              onClick={() => {
                setStep('request');
                setSecondsLeft(0);
              }}
              block
              className="otp-back-btn"
            >
              Back
            </Button>
          </Space>
        )}
      </Form>

      <Typography.Text style={{ color: '#9ab8e6' }}>
        External users: register email, wait for admin approval, then use OTP login.
      </Typography.Text>
    </Space>
  );
};

export default ExternalOtpForm;
