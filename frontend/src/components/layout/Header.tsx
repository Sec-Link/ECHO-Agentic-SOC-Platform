'use client';

import React from 'react';
import { Avatar, Button, Tag } from 'antd';
import { MoonOutlined, SunOutlined } from '@ant-design/icons';

export default function Header({
  username,
  impersonation,
  isReadonly,
  isDarkTheme,
  onToggleTheme,
  onOpenProfile,
  onClearImpersonation,
  onLogout,
}: {
  username: string | null;
  impersonation: { userId: number; username: string; permissions: string[] } | null;
  isReadonly: boolean;
  isDarkTheme: boolean;
  onToggleTheme: () => void;
  onOpenProfile: () => void;
  onClearImpersonation: () => void;
  onLogout: () => void;
}) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        background: 'var(--bg-header)',
        padding: '0 24px',
        borderBottom: 'none',
        color: 'var(--text-primary)',
        height: '100%',
        transition: 'background-color 180ms ease, color 180ms ease',
      }}
    >
      <div />
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button
          type="default"
          icon={isDarkTheme ? <SunOutlined /> : <MoonOutlined />}
          onClick={onToggleTheme}
          style={{
            background: 'var(--toggle-bg)',
            borderColor: 'var(--toggle-border)',
            color: 'var(--text-primary)',
          }}
          aria-label="Toggle dark mode"
        >
          {isDarkTheme ? 'Light' : 'Dark'}
        </Button>
        <Avatar
          style={{ background: 'var(--avatar-bg)', color: 'var(--text-contrast)', fontWeight: 700, cursor: 'pointer' }}
          onClick={onOpenProfile}
        >
          {(username && username[0]) ? String(username[0]).toUpperCase() : 'U'}
        </Avatar>
        <div style={{ color: 'var(--text-primary)', fontWeight: 600, cursor: 'pointer' }} onClick={onOpenProfile}>
          {username || 'User'}
        </div>
        {isReadonly ? <Tag color="blue">Readonly</Tag> : null}
        {impersonation ? <Tag color="orange">Impersonating: {impersonation.username}</Tag> : null}
        {impersonation ? <Button onClick={onClearImpersonation}>Exit impersonation</Button> : null}
        <Button type="primary" onClick={onLogout} style={{ background: '#ff4d4f', border: 'none' }}>
          Exit
        </Button>
      </div>
    </div>
  );
}
