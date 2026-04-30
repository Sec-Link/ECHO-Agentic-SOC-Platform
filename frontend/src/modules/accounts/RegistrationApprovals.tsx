import React, { useEffect, useState } from 'react';
import { Button, Card, Input, Modal, Space, Switch, Table, Tag, Typography, message } from 'antd';
import { useRouter } from 'next/navigation';
import {
  approveRegistrationRequest,
  getSystemSettings,
  listRegistrationRequests,
  rejectRegistrationRequest,
  updateSystemSettings,
} from 'services/accounts';

type RegistrationItem = {
  id: string;
  email: string;
  status: 'pending' | 'approved' | 'rejected';
  requested_at: string;
  reviewed_at?: string | null;
  reviewed_by_username?: string | null;
  review_reason?: string | null;
  approved_user_id?: number | null;
};

const RegistrationApprovals: React.FC = () => {
  const router = useRouter();
  const [rows, setRows] = useState<RegistrationItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>('pending');
  const [emailFilter, setEmailFilter] = useState<string>('');
  const [reasonModalOpen, setReasonModalOpen] = useState(false);
  const [reasonDraft, setReasonDraft] = useState('');
  const [rejectTargetId, setRejectTargetId] = useState<string | null>(null);
  const [autoApproveEnabled, setAutoApproveEnabled] = useState(true);
  const [savingAutoApprove, setSavingAutoApprove] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      const res = await listRegistrationRequests({
        status: statusFilter || undefined,
        email: emailFilter.trim() || undefined,
      });
      setRows(Array.isArray(res?.results) ? res.results : []);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Failed to load registration requests');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, [statusFilter]);

  useEffect(() => {
    const loadSettings = async () => {
      try {
        const res = await getSystemSettings();
        setAutoApproveEnabled(Boolean(res?.auto_approve_enabled));
      } catch (e: any) {
        message.error(e?.response?.data?.detail || 'Failed to load system settings');
      }
    };
    loadSettings();
  }, []);

  const onToggleAutoApprove = async (checked: boolean) => {
    setSavingAutoApprove(true);
    try {
      const res = await updateSystemSettings({ auto_approve_enabled: checked });
      setAutoApproveEnabled(Boolean(res?.auto_approve_enabled));
      message.success(`Auto approval ${checked ? 'enabled' : 'disabled'}.`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Failed to update system settings');
    } finally {
      setSavingAutoApprove(false);
    }
  };

  const approve = async (id: string) => {
    try {
      await approveRegistrationRequest(id, '');
      message.success('Registration approved and OTP sent.');
      reload();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Failed to approve request');
    }
  };

  const openReject = (id: string) => {
    setRejectTargetId(id);
    setReasonDraft('');
    setReasonModalOpen(true);
  };

  const confirmReject = async () => {
    if (!rejectTargetId) return;
    try {
      await rejectRegistrationRequest(rejectTargetId, reasonDraft);
      message.success('Registration rejected.');
      setReasonModalOpen(false);
      setRejectTargetId(null);
      reload();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Failed to reject request');
    }
  };

  return (
    <Card title="Registration Approvals">
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space wrap>
          <Space>
            <Typography.Text>Auto Approve Email Registration:</Typography.Text>
            <Switch
              checked={autoApproveEnabled}
              loading={savingAutoApprove}
              onChange={onToggleAutoApprove}
              checkedChildren="ON"
              unCheckedChildren="OFF"
            />
          </Space>
          <Input
            placeholder="Filter by email"
            value={emailFilter}
            onChange={(e) => setEmailFilter(e.target.value)}
            style={{ width: 260 }}
          />
          <Button onClick={reload}>Search</Button>
          <Button onClick={() => router.push('/settings/audit-logs')}>View Audit Logs</Button>
          <Button onClick={() => { setEmailFilter(''); setStatusFilter('pending'); }}>Reset</Button>
          <Space>
            <Typography.Text>Status:</Typography.Text>
            <Button type={statusFilter === 'pending' ? 'primary' : 'default'} onClick={() => setStatusFilter('pending')}>
              Pending
            </Button>
            <Button type={statusFilter === 'approved' ? 'primary' : 'default'} onClick={() => setStatusFilter('approved')}>
              Approved
            </Button>
            <Button type={statusFilter === 'rejected' ? 'primary' : 'default'} onClick={() => setStatusFilter('rejected')}>
              Rejected
            </Button>
          </Space>
        </Space>

        <Table
          rowKey="id"
          dataSource={rows}
          loading={loading}
          pagination={{ pageSize: 20 }}
          columns={[
            { title: 'Email', dataIndex: 'email' },
            {
              title: 'Status',
              dataIndex: 'status',
              render: (v: string) => (
                <Tag color={v === 'pending' ? 'gold' : v === 'approved' ? 'green' : 'red'}>{v}</Tag>
              ),
            },
            {
              title: 'Requested At',
              dataIndex: 'requested_at',
              render: (v: string) => (v ? new Date(v).toLocaleString() : '-'),
            },
            { title: 'Reviewed By', dataIndex: 'reviewed_by_username', render: (v: string) => v || '-' },
            { title: 'Reason', dataIndex: 'review_reason', render: (v: string) => v || '-' },
            {
              title: 'Actions',
              key: 'actions',
              render: (_, row: RegistrationItem) => (
                <Space>
                  <Button
                    type="primary"
                    size="small"
                    disabled={row.status !== 'pending'}
                    onClick={() => approve(row.id)}
                  >
                    Approve
                  </Button>
                  <Button danger size="small" disabled={row.status !== 'pending'} onClick={() => openReject(row.id)}>
                    Reject
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Space>

      <Modal
        title="Reject Registration Request"
        open={reasonModalOpen}
        onCancel={() => setReasonModalOpen(false)}
        onOk={confirmReject}
        okText="Reject"
        okButtonProps={{ danger: true }}
      >
        <Input.TextArea
          rows={4}
          placeholder="Optional reason"
          value={reasonDraft}
          onChange={(e) => setReasonDraft(e.target.value)}
        />
      </Modal>
    </Card>
  );
};

export default RegistrationApprovals;
