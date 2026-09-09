'use client';

import React from 'react';
import { Layout, Menu, Button, Tooltip, message } from 'antd';
import {
  Gauge,
  Search,
  Network,
  Zap,
  Shield,
  LayoutDashboard,
  Bell,
  Ticket,
  Server,
  Radar,
  Plug,
  Boxes,
  GitCompare,
  Cable,
  Workflow,
  Terminal,
  KeyRound,
  Bot,
  UserCheck,
  ScrollText,
  SlidersHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import { keyToPath, permissionByKey, type RouteKey } from 'route';

const { Sider } = Layout;

const RAIL_WIDTH = 64;

// Uniform lucide icon: inherits currentColor, sized to match AntD menu metrics.
const Ic = (Icon: React.ComponentType<any>) => (
  <Icon size={17} strokeWidth={1.9} style={{ verticalAlign: 'middle' }} />
);

// Semantic, unique icon per leaf route (SOC-oriented, no duplicates).
function iconByKey(key: RouteKey): React.ReactNode {
  switch (key) {
    case 'dashboard':
      return Ic(LayoutDashboard); // Overview
    case 'alerts':
      return Ic(Bell);
    case 'tickets':
      return Ic(Ticket);
    case 'assets':
      return Ic(Server);
    case 'detection':
      return Ic(Radar);
    case 'integrations':
      return Ic(Plug);
    case 'orchestrator':
      return Ic(Boxes);
    case 'correlation':
      return Ic(GitCompare);
    case 'interfaces':
      return Ic(Cable);
    case 'workflows':
      return Ic(Workflow);
    case 'workflow-executions':
      return Ic(Terminal);
    case 'permissions':
      return Ic(KeyRound); // Access Management
    case 'ai-assistant':
      return Ic(Bot);
    case 'registration-approvals':
      return Ic(UserCheck); // Approvals
    case 'audit-logs':
      return Ic(ScrollText);
    case 'system-settings':
      return Ic(SlidersHorizontal);
    default:
      return null;
  }
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
  canAccess: (perm?: string, key?: RouteKey) => boolean;
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
    orchestrator: labelOverrides.orchestrator || 'Orchestrator',
    interfaces: labelOverrides.interfaces || 'Interfaces',
    correlation: labelOverrides.correlation || 'Correlation',
    detection: labelOverrides.detection || 'Detection',
    permissions: labelOverrides.permissions || 'Access Management',
    'registration-approvals': labelOverrides['registration-approvals'] || 'Approvals',
    'audit-logs': labelOverrides['audit-logs'] || 'Audit Logs',
    'system-settings': labelOverrides['system-settings'] || 'System Settings',
    workflows: labelOverrides.workflows || 'Workflows',
    'workflow-executions': labelOverrides['workflow-executions'] || 'Executions',
    'ai-assistant': labelOverrides['ai-assistant'] || 'AI Assistant',
    profile: 'Profile',
  };

  // Group icons are distinct from their children (Monitoring ≠ Overview, etc.).
  const navGroups: Array<{ key: string; title: string; icon: React.ReactNode; items: RouteKey[] }> = [
    { key: 'monitorGroup', title: 'Monitoring', icon: Ic(Gauge), items: ['dashboard', 'alerts'] },
    { key: 'investigationGroup', title: 'Investigation', icon: Ic(Search), items: ['tickets', 'assets'] },
    {
      key: 'dataPipelineGroup',
      title: 'Data Pipeline',
      icon: Ic(Network),
      items: ['detection', 'integrations', 'orchestrator', 'correlation'],
    },
    {
      key: 'automationGroup',
      title: 'Automation',
      icon: Ic(Zap),
      items: ['interfaces', 'workflows', 'workflow-executions'],
    },
    {
      key: 'administrationGroup',
      title: 'Administration',
      icon: Ic(Shield),
      items: ['permissions', 'ai-assistant', 'registration-approvals', 'audit-logs', 'system-settings'],
    },
  ];

  // Build items[] so the collapsed rail renders native flyout popovers.
  const menuItems = navGroups
    .map((group) => {
      const visibleItems = group.items.filter((key) => canAccess(permissionByKey[key], key));
      if (visibleItems.length === 0) return null;
      const leaves = visibleItems.map((itemKey) => ({
        key: itemKey,
        icon: iconByKey(itemKey),
        label: routeLabel[itemKey],
      }));
      // When collapsed, prepend the category name as a group header inside the
      // hover flyout so the parent title is visible without expanding.
      const children = siderCollapsed
        ? [{ key: `${group.key}-title`, type: 'group', label: group.title }, ...leaves]
        : leaves;
      return {
        key: group.key,
        icon: group.icon,
        label: group.title,
        children,
      };
    })
    .filter(Boolean) as any[];

  return (
    <Sider
      width={siderWidth}
      collapsedWidth={RAIL_WIDTH}
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
      {/* Brand row — logo is home/refresh only. When expanded, the collapse
          toggle sits on this same row, far right. */}
      <div
        className="sidebar-brand-row"
        style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: siderCollapsed ? 'center' : 'space-between',
          padding: siderCollapsed ? '0' : '0 12px',
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
            src="/seclink-logo.png"
            alt="Argus logo"
            width={siderCollapsed ? 36 : 40}
            height={siderCollapsed ? 36 : 40}
            className="sidebar-brand-logo"
            style={{ width: siderCollapsed ? 36 : 40, height: siderCollapsed ? 36 : 40, borderRadius: 8, objectFit: 'contain' }}
          />
          {!siderCollapsed && <span className="argus-brand-wordmark argus-brand-wordmark-sidebar">Argus</span>}
        </div>
        {!siderCollapsed && (
          <Tooltip title="Collapse menu" placement="right">
            <Button
              type="text"
              size="small"
              className="sidebar-toggle-btn"
              icon={<PanelLeftClose size={18} strokeWidth={1.9} />}
              onClick={() => setSiderCollapsed(true)}
              aria-label="Collapse menu"
            />
          </Tooltip>
        )}
      </div>

      {/* Collapsed: dedicated expand button below the logo, centered to align
          with the icon rail, kept compact to match the icon rhythm. */}
      {siderCollapsed && (
        <div className="sidebar-rail-toggle">
          <Tooltip title="Expand menu" placement="right">
            <Button
              type="text"
              size="small"
              className="sidebar-toggle-btn"
              icon={<PanelLeftOpen size={18} strokeWidth={1.9} />}
              onClick={() => setSiderCollapsed(false)}
              aria-label="Expand menu"
            />
          </Tooltip>
        </div>
      )}

      <Menu
        mode="inline"
        inlineCollapsed={siderCollapsed}
        className="siem-menu-pale"
        selectedKeys={[selectedKey]}
        // openKeys only apply in expanded mode; collapsed uses hover popovers.
        openKeys={siderCollapsed ? undefined : openKeys}
        onOpenChange={(keys) => {
          if (!siderCollapsed) setOpenKeys(keys as string[]);
        }}
        items={menuItems}
        onClick={({ key }) => {
          const nextKey = String(key) as RouteKey;
          const nextPerm = permissionByKey[nextKey];
          if (nextPerm && !canAccess(nextPerm, nextKey)) {
            message.warning('No permission to access this feature.');
            return;
          }
          onNavigate(keyToPath[nextKey] || '/dashboard');
        }}
        style={{ borderRight: 'none', background: 'transparent' }}
      />

      {/* Drag-to-resize handle: expanded only. */}
      {!siderCollapsed ? (
        <div
          onMouseDown={(e) => {
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
      ) : null}
    </Sider>
  );
}
