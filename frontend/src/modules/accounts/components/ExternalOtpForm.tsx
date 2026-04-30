import React, { useEffect, useMemo, useState } from 'react';
import { Form, Input, Button, message, Modal, Typography } from 'antd';
import { MailOutlined, SafetyCertificateOutlined, ReloadOutlined } from '@ant-design/icons';
import { getGuestEmailStatus, registerEmail, requestOtp, verifyOtp } from 'services/accounts';

interface Props {
  onLoginSuccess: () => void;
}

const RESEND_SECONDS = 60;

const ExternalOtpForm: React.FC<Props> = ({ onLoginSuccess }) => {
  const [email, setEmail] = useState('');
  const [step, setStep] = useState<'idle' | 'verify'>('idle');
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [checking, setChecking] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [nextAction, setNextAction] = useState<'register' | 'send_otp' | null>(null);
  const [mailHintOpen, setMailHintOpen] = useState(false);
  const [mailHintText, setMailHintText] = useState('');

  useEffect(() => {
    if (secondsLeft <= 0) return;
    const timer = window.setInterval(() => {
      setSecondsLeft((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [secondsLeft]);

  const normalizedEmail = useMemo(() => (email || '').trim().toLowerCase(), [email]);
  const emailIsValid = useMemo(() => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail), [normalizedEmail]);
  const canResend = step === 'verify' && secondsLeft <= 0 && !sending;

  useEffect(() => {
    if (step !== 'idle') return;
    if (!emailIsValid) {
      setNextAction(null);
      return;
    }
    setNextAction(null);
    let active = true;
    const handle = window.setTimeout(async () => {
      setChecking(true);
      try {
        const res = await getGuestEmailStatus(normalizedEmail);
        if (active) setNextAction(res?.next_action === 'send_otp' ? 'send_otp' : 'register');
      } catch {
        if (active) setNextAction('register');
      } finally {
        if (active) setChecking(false);
      }
    }, 260);

    return () => {
      active = false;
      window.clearTimeout(handle);
    };
  }, [normalizedEmail, emailIsValid, step]);

  const openMailHint = (text: string) => {
    setMailHintText(text);
    setMailHintOpen(true);
  };

  const sendOtp = async () => {
    if (!emailIsValid) {
      message.warning('Please enter a valid email');
      return;
    }
    setSending(true);
    try {
      await requestOtp(normalizedEmail);
      setStep('verify');
      setSecondsLeft(RESEND_SECONDS);
      openMailHint('A verification email has been sent. Please check your inbox and enter the OTP code.');
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

  const submitRegistration = async () => {
    if (!emailIsValid) {
      message.warning('Please enter a valid email');
      return;
    }
    setSending(true);
    try {
      await registerEmail(normalizedEmail);
      openMailHint('A confirmation email has been sent. Please check your inbox and follow the instructions.');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Registration submit failed');
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

  return (
    <>
      <Form layout="vertical" className="login-form-panel" onFinish={verify} aria-live="polite">
        <Form.Item label={<span style={{ color: '#9ab8e6' }}>Email</span>} required>
          <Input
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setNextAction(null);
              if (step !== 'idle') {
                setStep('idle');
                setSecondsLeft(0);
              }
            }}
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

        {step === 'idle' ? (
          !emailIsValid ? null : checking && !nextAction ? (
            <Button block loading aria-busy>
              Checking...
            </Button>
          ) : nextAction === 'send_otp' ? (
            <Button type="primary" loading={sending || checking} onClick={sendOtp} block aria-busy={sending || checking}>
              {sending ? 'Sending OTP...' : checking ? 'Checking...' : 'Send OTP'}
            </Button>
          ) : nextAction === 'register' ? (
            <Button type="primary" loading={sending || checking} onClick={submitRegistration} block aria-busy={sending || checking}>
              {sending ? 'Registering...' : checking ? 'Checking...' : 'Register Email'}
            </Button>
          ) : null
        ) : (
          <>
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
                setStep('idle');
                setSecondsLeft(0);
              }}
              block
              className="otp-back-btn"
            >
              Back
            </Button>
          </>
        )}

        {step === 'idle' && !emailIsValid ? (
          <Typography.Text style={{ color: '#9ab8e6' }}>Enter your email to continue</Typography.Text>
        ) : null}
      </Form>

      <Modal
        open={mailHintOpen}
        onCancel={() => setMailHintOpen(false)}
        footer={null}
        centered
        className="login-mail-modal"
        closeIcon={<span className="login-mail-modal-close-x">×</span>}
        destroyOnClose
      >
        <div className="login-mail-modal-content">
          <Typography.Title level={4} style={{ marginTop: 0, marginBottom: 10, color: '#d8e8ff' }}>
            Email Sent
          </Typography.Title>
          <Typography.Paragraph style={{ color: '#b9cff4', marginBottom: 18 }}>
            {mailHintText}
          </Typography.Paragraph>
          <Button type="primary" block onClick={() => setMailHintOpen(false)}>
            Got it
          </Button>
        </div>
      </Modal>
    </>
  );
};

export default ExternalOtpForm;
