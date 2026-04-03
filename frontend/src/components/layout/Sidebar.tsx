'use client';

import React from 'react';
import { Layout, Menu, Button, message } from 'antd';
import {
  DashboardOutlined,
  BellOutlined,
  UnorderedListOutlined,
  AppstoreOutlined,
  TeamOutlined,
  LineChartOutlined,
  LockOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  BranchesOutlined,
  HddOutlined,
  DeploymentUnitOutlined,
  RadarChartOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import { keyToPath, permissionByKey, type RouteKey } from 'route';

const { Sider } = Layout;

const DatabaseSvg: React.FC<{ style?: React.CSSProperties }> = ({ style }) => (
  <svg
    viewBox="0 0 24 24"
    width="16"
    height="16"
    style={style}
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    stroke="currentColor"
  >
    <ellipse cx="12" cy="5" rx="8" ry="3" strokeWidth="1.6" />
    <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" strokeWidth="1.6" />
    <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" strokeWidth="1.6" />
  </svg>
);

function iconByKey(key: RouteKey) {
  if (key === 'dashboard') return <DashboardOutlined />;
  if (key === 'alerts') return <BellOutlined />;
  if (key === 'tickets') return <UnorderedListOutlined />;
  if (key === 'assets') return <HddOutlined />;
  if (key === 'integrations') return <AppstoreOutlined />;
  if (key === 'dashboards') return <DashboardOutlined />;
  if (key === 'datasources') return <DatabaseSvg style={{ marginRight: 8 }} />;
  if (key === 'orchestrator') return <DeploymentUnitOutlined />;
  if (key === 'interfaces') return <ApiOutlined />;
  if (key === 'correlation') return <LineChartOutlined />;
  if (key === 'workflows') return <BranchesOutlined />;
  if (key === 'workflow-executions') return <RadarChartOutlined />;
  if (key === 'permissions') return <LockOutlined />;
  if (key === 'ai-assistant') return <TeamOutlined />;
  return null;
}

export default function Sidebar({
  siderWidth,
  siderCollapsed,
  openKeys,
  selectedKey,
  settingsItems,
  setSiderCollapsed,
  setOpenKeys,
  setIsResizing,
  setSiderWidthCustomized,
  canAccess,
  onNavigate,
}: {
  siderWidth: number;
  siderCollapsed: boolean;
  openKeys: string[];
  selectedKey: string;
  settingsItems: Array<{ key: RouteKey; label: string }>;
  setSiderCollapsed: (v: boolean) => void;
  setOpenKeys: (keys: string[]) => void;
  setIsResizing: (v: boolean) => void;
  setSiderWidthCustomized: (v: boolean) => void;
  canAccess: (perm?: string) => boolean;
  onNavigate: (path: string) => void;
}) {
  const labelOverrides = Object.fromEntries(settingsItems.map((item) => [item.key, item.label])) as Partial<
    Record<RouteKey, string>
  >;
  const routeLabel: Record<RouteKey, string> = {
    dashboard: 'Overview',
    alerts: 'Alerts',
    tickets: 'Tickets',
    assets: 'Assets',
    integrations: labelOverrides.integrations || 'Integrations',
    dashboards: labelOverrides.dashboards || 'Dashboard List',
    datasources: labelOverrides.datasources || 'Data Sources',
    orchestrator: labelOverrides.orchestrator || 'Orchestrator',
    interfaces: labelOverrides.interfaces || 'Interfaces',
    correlation: labelOverrides.correlation || 'Correlation',
    permissions: labelOverrides.permissions || 'Access Management',
    workflows: labelOverrides.workflows || 'Workflows',
    'workflow-executions': labelOverrides['workflow-executions'] || 'Workflow Executions',
    'ai-assistant': labelOverrides['ai-assistant'] || 'AI Assistant',
    profile: 'Profile',
  };

  const navGroups: Array<{ key: string; title: string; icon: React.ReactNode; items: RouteKey[] }> = [
    {
      key: 'monitorGroup',
      title: 'Monitoring',
      icon: <DashboardOutlined />,
      items: ['dashboard', 'alerts'],
    },
    {
      key: 'investigationGroup',
      title: 'Investigation',
      icon: <TeamOutlined />,
      items: ['tickets', 'assets'],
    },
    {
      key: 'dataPlatformGroup',
      title: 'Data & Platform',
      icon: <AppstoreOutlined />,
      items: ['integrations', 'datasources'],
    },
    {
      key: 'automationGroup',
      title: 'Detection & Automation',
      icon: <BranchesOutlined />,
      items: ['correlation', 'orchestrator', 'interfaces', 'workflows', 'workflow-executions'],
    },
    {
      key: 'adminGroup',
      title: 'Administration',
      icon: <LockOutlined />,
      items: ['permissions', 'ai-assistant'],
    },
  ];

  return (
    <>
      <Sider
        width={siderWidth}
        collapsedWidth={0}
        collapsed={siderCollapsed}
        trigger={null}
        style={{
          background: 'var(--bg-sidebar)',
          position: 'sticky',
          top: 0,
          alignSelf: 'flex-start',
          height: '100vh',
          overflowY: 'auto',
          overflowX: 'hidden',
          transition: 'width 240ms cubic-bezier(0.22, 1, 0.36, 1), background-color 180ms ease',
        }}
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 12px',
            fontWeight: 700,
          }}
        >
          <div
            onClick={() => onNavigate('/dashboard')}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onNavigate('/dashboard');
              }
            }}
            style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' }}
            aria-label="Go to dashboard"
          >
            <img
              src="/seclink-logo.jpg"
              alt="SECLINK logo"
              width={40}
              height={40}
              style={{ width: 40, height: 40, borderRadius: 8, objectFit: 'cover' }}
            />
            <span style={{ fontWeight: 900, letterSpacing: 0.6, fontSize: 22, color: 'var(--text-primary)' }}>
              SIEM
            </span>
          </div>
          <Button
            type="text"
            size="small"
            icon={<MenuFoldOutlined />}
            onClick={() => setSiderCollapsed(true)}
            style={{ color: 'var(--text-primary)' }}
          />
        </div>

        <Menu
          mode="inline"
          className="siem-menu-pale"
          selectedKeys={[selectedKey]}
          openKeys={openKeys}
          onOpenChange={(keys) => setOpenKeys(keys as string[])}
          onClick={({ key }) => {
            const nextKey = String(key) as RouteKey;
            const nextPerm = permissionByKey[nextKey];
            if (nextPerm && !canAccess(nextPerm)) {
              message.warning('No permission to access this feature.');
              return;
            }
            onNavigate(keyToPath[nextKey] || '/dashboard');
          }}
          style={{ borderRight: 'none', background: 'transparent' }}
        >
          {navGroups.map((group) => {
            const visibleItems = group.items.filter((key) => canAccess(permissionByKey[key]));
            if (visibleItems.length === 0) return null;
            return (
              <Menu.SubMenu key={group.key} icon={group.icon} title={group.title}>
                {visibleItems.map((itemKey) => (
                  <Menu.Item key={itemKey} icon={iconByKey(itemKey)}>
                    {routeLabel[itemKey]}
                  </Menu.Item>
                ))}
              </Menu.SubMenu>
            );
          })}
        </Menu>

        <div
          onMouseDown={(e) => {
            if (siderCollapsed) return;
            e.preventDefault();
            setIsResizing(true);
            setSiderWidthCustomized(true);
          }}
          style={{
            position: 'absolute',
            top: 0,
            right: 0,
            width: 6,
            height: '100%',
            cursor: 'col-resize',
            background: 'var(--resizer-bg)',
          }}
        />
      </Sider>

      {siderCollapsed ? (
        <Button
          type="primary"
          icon={<MenuUnfoldOutlined />}
          onClick={() => setSiderCollapsed(false)}
          style={{ position: 'fixed', left: 10, top: 12, zIndex: 1000 }}
        />
      ) : null}
    </>
  );
}
