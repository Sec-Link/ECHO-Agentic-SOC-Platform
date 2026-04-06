import React, { useEffect, useMemo, useState } from 'react'
import { Card, Col, Row, Select, Space, Tabs, Transfer, Button, message, Tag, Tooltip, Popconfirm, Badge, Input, Modal, List, Typography, Switch, Divider } from 'antd'
import { PlusCircleOutlined, DeleteOutlined, FilterOutlined } from '@ant-design/icons'
import { createGroup, createUser, deleteGroup, deleteUser, getGroupPermissions, getUserGroups, getUserPermissions, listGroups, listPermissions, listUsers, resetUserPassword, updateGroup, updateGroupPermissions, updateUser, updateUserGroups, updateUserPermissions } from 'services/accounts'

type BasicUser = {
  id: number
  username: string
  email?: string
  first_name?: string
  last_name?: string
  is_active?: boolean
  is_staff?: boolean
  is_superuser?: boolean
  groups?: number[]
}
type BasicGroup = { id: number; name: string; permissions?: number[] }
type PermissionItem = { id: number; name: string; codename: string; app_label: string; model: string }

const Permissions: React.FC = () => {
  const [users, setUsers] = useState<BasicUser[]>([])
  const [groups, setGroups] = useState<BasicGroup[]>([])
  const [permissions, setPermissions] = useState<PermissionItem[]>([])
  const [selectedUserId, setSelectedUserId] = useState<number | undefined>(undefined)
  const [selectedGroupId, setSelectedGroupId] = useState<number | undefined>(undefined)
  const [userPermissionIds, setUserPermissionIds] = useState<number[]>([])
  const [userGroupIds, setUserGroupIds] = useState<number[]>([])
  const [userGroupPermissionIds, setUserGroupPermissionIds] = useState<number[]>([])
  const [userEffectivePermissionIds, setUserEffectivePermissionIds] = useState<number[]>([])
  const [groupPermissionIds, setGroupPermissionIds] = useState<number[]>([])
  const [groupMemberIds, setGroupMemberIds] = useState<number[]>([])
  const [userPermissionFilter, setUserPermissionFilter] = useState<string>('all')
  const [groupPermissionFilter, setGroupPermissionFilter] = useState<string>('all')
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<string>('user')
  const [groupSearch, setGroupSearch] = useState<string>('')
  const [createGroupOpen, setCreateGroupOpen] = useState(false)
  const [createGroupName, setCreateGroupName] = useState<string>('')
  const [createGroupSaving, setCreateGroupSaving] = useState(false)
  const [groupNameDraft, setGroupNameDraft] = useState<string>('')
  const [groupNameSaving, setGroupNameSaving] = useState(false)
  const [groupDeleteSaving, setGroupDeleteSaving] = useState(false)
  const [userSearch, setUserSearch] = useState<string>('')
  const [userPage, setUserPage] = useState<number>(1)
  const [userPageSize, setUserPageSize] = useState<number>(10)
  const [createUserOpen, setCreateUserOpen] = useState(false)
  const [createUserSaving, setCreateUserSaving] = useState(false)
  const [resetPasswordOpen, setResetPasswordOpen] = useState(false)
  const [resetPasswordSaving, setResetPasswordSaving] = useState(false)
  const [resetPasswordDraft, setResetPasswordDraft] = useState({
    new_password: '',
    confirm_password: '',
  })
  const [createUserDraft, setCreateUserDraft] = useState({
    username: '',
    email: '',
    first_name: '',
    last_name: '',
    is_active: true,
    is_staff: false,
    is_superuser: false,
    groups: [] as number[],
  })
  const [userDraft, setUserDraft] = useState({
    username: '',
    email: '',
    first_name: '',
    last_name: '',
    is_active: true,
    is_staff: false,
    is_superuser: false,
    groups: [] as number[],
  })
  const [userSaving, setUserSaving] = useState(false)
  const [userDeleteSaving, setUserDeleteSaving] = useState(false)
  const [currentUsername, setCurrentUsername] = useState<string | null>(() => {
    try {
      return localStorage.getItem('siem_username')
    } catch {
      return null
    }
  })
  const [viewAs, setViewAs] = useState<{ userId: number; username: string; permissions: string[] } | null>(() => {
    try {
      const raw = localStorage.getItem('siem_impersonation')
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  })

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const [u, g, p] = await Promise.all([
          listUsers(),
          listGroups(),
          listPermissions({ common_only: true }),
        ])
        setUsers(u || [])
        setGroups(g || [])
        setPermissions(p || [])
      } catch (e: any) {
        message.error(e?.message || 'Failed to load permission data')
      } finally {
        setLoading(false)
      }
    }
    load()
    try {
      setCurrentUsername(localStorage.getItem('siem_username'))
    } catch {}
  }, [])

  useEffect(() => {
    if (!selectedUserId) return
    const loadUser = async () => {
      setLoading(true)
      try {
        const [perms, groupsRes] = await Promise.all([
          getUserPermissions(selectedUserId),
          getUserGroups(selectedUserId),
        ])
        setUserPermissionIds(perms?.permission_ids || [])
        setUserGroupIds(groupsRes?.group_ids || [])
      } catch (e: any) {
        message.error(e?.message || 'Failed to load user permissions')
      } finally {
        setLoading(false)
      }
    }
    loadUser()
  }, [selectedUserId])

  useEffect(() => {
    if (!selectedGroupId) return
    const loadGroup = async () => {
      setLoading(true)
      try {
        const res = await getGroupPermissions(selectedGroupId)
        setGroupPermissionIds(res?.permission_ids || [])
      } catch (e: any) {
        message.error(e?.message || 'Failed to load group permissions')
      } finally {
        setLoading(false)
      }
    }
    loadGroup()
  }, [selectedGroupId])

  useEffect(() => {
    const group = groups.find((g) => g.id === selectedGroupId)
    if (group) setGroupNameDraft(group.name || '')
    else setGroupNameDraft('')
  }, [groups, selectedGroupId])

  useEffect(() => {
    const user = users.find((u) => u.id === selectedUserId)
    if (!user) {
      setUserDraft({
        username: '',
        email: '',
        first_name: '',
        last_name: '',
        is_active: true,
        is_staff: false,
        is_superuser: false,
        groups: [],
      })
      return
    }
    setUserDraft({
      username: user.username || '',
      email: user.email || '',
      first_name: (user as any).first_name || '',
      last_name: (user as any).last_name || '',
      is_active: (user as any).is_active ?? true,
      is_staff: user.is_staff ?? false,
      is_superuser: user.is_superuser ?? false,
      groups: Array.isArray(user.groups) ? user.groups : [],
    })
  }, [users, selectedUserId])

  useEffect(() => {
    if (!selectedGroupId) {
      setGroupMemberIds([])
      return
    }
    const members = users
      .filter((u) => Array.isArray(u.groups) && u.groups.includes(selectedGroupId))
      .map((u) => u.id)
    setGroupMemberIds(members)
  }, [selectedGroupId, users])

  const permissionCategoryOptions = useMemo(() => {
    const labelMap: Record<string, string> = {
      tickets: 'Tickets',
      dashboards: 'Dashboards',
      integrations: 'Integrations',
      alerts: 'Alerts',
      datasource: 'Data Sources',
      orchestrator: 'Orchestrator',
      correlation: 'Correlation',
      accounts: 'Accounts',
      users: 'Users',
    }
    const labels = Array.from(new Set(permissions.map((p) => p.app_label))).sort()
    return [
      { label: 'All', value: 'all' },
      ...labels.map((key) => ({ label: labelMap[key] || key, value: key })),
    ]
  }, [permissions])

  const filteredUsers = useMemo(() => {
    const keyword = userSearch.trim().toLowerCase()
    if (!keyword) return users
    return users.filter((u) => u.username.toLowerCase().includes(keyword))
  }, [userSearch, users])

  const sortedUsers = useMemo(() => {
    const list = [...filteredUsers]
    list.sort((a, b) => {
      if (a.is_superuser && !b.is_superuser) return -1
      if (!a.is_superuser && b.is_superuser) return 1
      return a.username.localeCompare(b.username)
    })
    return list
  }, [filteredUsers])

  const filteredGroups = useMemo(() => {
    const keyword = groupSearch.trim().toLowerCase()
    if (!keyword) return groups
    return groups.filter((g) => g.name.toLowerCase().includes(keyword))
  }, [groupSearch, groups])
  const groupMemberCounts = useMemo(() => {
    const counts: Record<number, number> = {}
    users.forEach((u) => {
      if (!Array.isArray(u.groups)) return
      u.groups.forEach((groupId) => {
        counts[groupId] = (counts[groupId] || 0) + 1
      })
    })
    return counts
  }, [users])

  const filteredUserPermissions = useMemo(() => {
    if (userPermissionFilter === 'all') return permissions
    return permissions.filter((p) => p.app_label === userPermissionFilter)
  }, [permissions, userPermissionFilter])

  const filteredGroupPermissions = useMemo(() => {
    if (groupPermissionFilter === 'all') return permissions
    return permissions.filter((p) => p.app_label === groupPermissionFilter)
  }, [permissions, groupPermissionFilter])

  const userCategoryLabel = useMemo(() => {
    return (permissionCategoryOptions.find((o) => o.value === userPermissionFilter)?.label || 'All') as string
  }, [permissionCategoryOptions, userPermissionFilter])

  const groupCategoryLabel = useMemo(() => {
    return (permissionCategoryOptions.find((o) => o.value === groupPermissionFilter)?.label || 'All') as string
  }, [permissionCategoryOptions, groupPermissionFilter])

  const userCategoryAssignedCount = useMemo(() => {
    const ids = new Set(filteredUserPermissions.map((p) => p.id))
    return userPermissionIds.filter((id) => ids.has(id)).length
  }, [filteredUserPermissions, userPermissionIds])

  const groupCategoryAssignedCount = useMemo(() => {
    const ids = new Set(filteredGroupPermissions.map((p) => p.id))
    return groupPermissionIds.filter((id) => ids.has(id)).length
  }, [filteredGroupPermissions, groupPermissionIds])

  const userPermissionTransferData = useMemo(() => {
    return filteredUserPermissions.map((p) => ({
      key: String(p.id),
      title: `${p.app_label}.${p.codename}`,
      description: p.name,
    }))
  }, [filteredUserPermissions])

  const groupPermissionTransferData = useMemo(() => {
    return filteredGroupPermissions.map((p) => ({
      key: String(p.id),
      title: `${p.app_label}.${p.codename}`,
      description: p.name,
    }))
  }, [filteredGroupPermissions])

  const effectivePermissionItems = useMemo(() => {
    const effective = new Set(userEffectivePermissionIds)
    return permissions.filter((p) => effective.has(p.id))
  }, [permissions, userEffectivePermissionIds])

  const directPermissionItems = useMemo(() => {
    const direct = new Set(userPermissionIds)
    return permissions.filter((p) => direct.has(p.id))
  }, [permissions, userPermissionIds])

  const groupPermissionItems = useMemo(() => {
    const group = new Set(userGroupPermissionIds)
    return permissions.filter((p) => group.has(p.id))
  }, [permissions, userGroupPermissionIds])

  const groupMemberTransferData = useMemo(() => {
    return users.map((u) => ({
      key: String(u.id),
      title: u.username,
      description: u.email || (u.is_superuser ? 'Superuser' : u.is_staff ? 'Staff' : 'User'),
    }))
  }, [users])

  const setViewAsUser = async () => {
    if (!selectedUserId) return
    const user = users.find((u) => u.id === selectedUserId)
    if (!user) return
    if (currentUsername && user.username === currentUsername) {
      message.warning('Cannot impersonate the current account')
      return
    }
    try {
      const [res, groupsRes] = await Promise.all([
        getUserPermissions(selectedUserId),
        getUserGroups(selectedUserId),
      ])
      const directPerms: PermissionItem[] = res?.permissions || []
      const groupIds: number[] = groupsRes?.group_ids || []
      const groupPermsLists = await Promise.all(groupIds.map((gid) => getGroupPermissions(gid)))
      const groupPerms: PermissionItem[] = groupPermsLists.flatMap((gp) => gp?.permissions || [])
      const merged = [...directPerms, ...groupPerms]
      const effective = Array.from(new Set(merged.map((p) => `${p.app_label}.${p.codename}`)))
      const payload = { userId: user.id, username: user.username, permissions: effective }
      localStorage.setItem('siem_impersonation', JSON.stringify(payload))
      setViewAs(payload)
      window.dispatchEvent(new Event('siem_impersonation_changed'))
      message.success(`Impersonating user: ${user.username}`)
    } catch (e: any) {
      message.error(e?.message || 'Failed to impersonate user')
    }
  }

  const clearViewAs = () => {
    try {
      localStorage.removeItem('siem_impersonation')
    } catch {}
    setViewAs(null)
    window.dispatchEvent(new Event('siem_impersonation_changed'))
    message.success('Impersonation cleared')
  }

  const updateGroupMembers = async (nextIds: number[]) => {
    if (!selectedGroupId) return
    setGroupMemberIds(nextIds)
    const selectedSet = new Set(nextIds)
    const updates = users.map(async (u) => {
      const currentGroups = Array.isArray(u.groups) ? u.groups : []
      const hasGroup = currentGroups.includes(selectedGroupId)
      const shouldHave = selectedSet.has(u.id)
      if (hasGroup === shouldHave) return null
      const nextGroups = shouldHave
        ? Array.from(new Set([...currentGroups, selectedGroupId]))
        : currentGroups.filter((gid) => gid !== selectedGroupId)
      await updateUserGroups(u.id, nextGroups)
      return { userId: u.id, groups: nextGroups }
    })
    try {
      const results = (await Promise.all(updates)).filter(Boolean) as { userId: number; groups: number[] }[]
      if (results.length) {
        setUsers((prev) =>
          prev.map((u) => {
            const updated = results.find((r) => r.userId === u.id)
            return updated ? { ...u, groups: updated.groups } : u
          })
        )
        const updatedSelected = results.find((r) => r.userId === selectedUserId)
        if (updatedSelected) setUserGroupIds(updatedSelected.groups)
      }
      message.success('Group members updated')
    } catch (e: any) {
      message.error(e?.message || 'Failed to update group members')
    }
  }

  const loadUserEffectivePermissions = async (groupIds: number[]) => {
    if (!groupIds.length) {
      setUserGroupPermissionIds([])
      setUserEffectivePermissionIds(userPermissionIds)
      return
    }
    try {
      const groupPermsLists = await Promise.all(groupIds.map((gid) => getGroupPermissions(gid)))
      const groupPermIds = Array.from(new Set(groupPermsLists.flatMap((gp) => gp?.permission_ids || [])))
      const effective = Array.from(new Set([...userPermissionIds, ...groupPermIds]))
      setUserGroupPermissionIds(groupPermIds)
      setUserEffectivePermissionIds(effective)
    } catch {
      setUserGroupPermissionIds([])
      setUserEffectivePermissionIds(userPermissionIds)
    }
  }

  const handleCreateGroup = async () => {
    const name = createGroupName.trim()
    if (!name) {
      message.warning('Group name is required')
      return
    }
    setCreateGroupSaving(true)
    try {
      const created = await createGroup({ name, permissions: [] })
      const updated = await listGroups()
      setGroups(updated || [])
      if (created?.id) setSelectedGroupId(created.id)
      setCreateGroupOpen(false)
      setCreateGroupName('')
      message.success('Group created')
    } catch (e: any) {
      message.error(e?.message || 'Failed to create group')
    } finally {
      setCreateGroupSaving(false)
    }
  }

  const handleUpdateGroupName = async () => {
    if (!selectedGroupId) return
    const name = groupNameDraft.trim()
    if (!name) {
      message.warning('Group name is required')
      return
    }
    setGroupNameSaving(true)
    try {
      await updateGroup(selectedGroupId, { name })
      setGroups((prev) => prev.map((g) => (g.id === selectedGroupId ? { ...g, name } : g)))
      message.success('Group updated')
    } catch (e: any) {
      message.error(e?.message || 'Failed to update group')
    } finally {
      setGroupNameSaving(false)
    }
  }

  const handleDeleteGroup = async () => {
    if (!selectedGroupId) return
    setGroupDeleteSaving(true)
    try {
      await deleteGroup(selectedGroupId)
      const remaining = groups.filter((g) => g.id !== selectedGroupId)
      setGroups(remaining)
      setSelectedGroupId(remaining[0]?.id)
      message.success('Group deleted')
    } catch (e: any) {
      message.error(e?.message || 'Failed to delete group')
    } finally {
      setGroupDeleteSaving(false)
    }
  }

  const applyUserCategory = async (mode: 'assign' | 'clear') => {
    if (!selectedUserId) return
    const categoryIds = filteredUserPermissions.map((p) => p.id)
    const categorySet = new Set(categoryIds)
    const nextIds =
      mode === 'assign'
        ? Array.from(new Set([...userPermissionIds, ...categoryIds]))
        : userPermissionIds.filter((id) => !categorySet.has(id))
    setUserPermissionIds(nextIds)
    try {
      await updateUserPermissions(selectedUserId, nextIds)
      message.success('User permissions updated')
      loadUserEffectivePermissions(userGroupIds)
    } catch (e: any) {
      message.error(e?.message || 'Failed to update user permissions')
    }
  }

  const applyGroupCategory = async (mode: 'assign' | 'clear') => {
    if (!selectedGroupId) return
    const categoryIds = filteredGroupPermissions.map((p) => p.id)
    const categorySet = new Set(categoryIds)
    const nextIds =
      mode === 'assign'
        ? Array.from(new Set([...groupPermissionIds, ...categoryIds]))
        : groupPermissionIds.filter((id) => !categorySet.has(id))
    setGroupPermissionIds(nextIds)
    try {
      await updateGroupPermissions(selectedGroupId, nextIds)
      message.success('Group permissions updated')
    } catch (e: any) {
      message.error(e?.message || 'Failed to update group permissions')
    }
  }

  const handleCreateUser = async () => {
    const payload = {
      username: createUserDraft.username.trim(),
      email: createUserDraft.email.trim() || undefined,
      first_name: createUserDraft.first_name.trim() || undefined,
      last_name: createUserDraft.last_name.trim() || undefined,
      is_active: createUserDraft.is_active,
      is_staff: createUserDraft.is_staff,
      is_superuser: createUserDraft.is_superuser,
      groups: createUserDraft.groups,
    }
    if (!payload.username) {
      message.warning('Username is required')
      return
    }
    setCreateUserSaving(true)
    try {
      const created = await createUser(payload)
      const updated = await listUsers()
      setUsers(updated || [])
      if (created?.id) setSelectedUserId(created.id)
      setCreateUserOpen(false)
      setCreateUserDraft({
        username: '',
        email: '',
        first_name: '',
        last_name: '',
        is_active: true,
        is_staff: false,
        is_superuser: false,
        groups: [],
      })
      message.success('User created')
    } catch (e: any) {
      message.error(e?.message || 'Failed to create user')
    } finally {
      setCreateUserSaving(false)
    }
  }

  const handleUpdateUser = async () => {
    if (!selectedUserId) return
    const payload = {
      username: userDraft.username.trim(),
      email: userDraft.email.trim() || undefined,
      first_name: userDraft.first_name.trim() || undefined,
      last_name: userDraft.last_name.trim() || undefined,
      is_active: userDraft.is_active,
      is_staff: userDraft.is_staff,
      is_superuser: userDraft.is_superuser,
      groups: userDraft.groups,
    }
    if (!payload.username) {
      message.warning('Username is required')
      return
    }
    setUserSaving(true)
    try {
      await updateUser(selectedUserId, payload)
      setUsers((prev) => prev.map((u) => (u.id === selectedUserId ? { ...u, ...payload } : u)))
      setUserGroupIds(payload.groups || [])
      message.success('User updated')
    } catch (e: any) {
      message.error(e?.message || 'Failed to update user')
    } finally {
      setUserSaving(false)
    }
  }

  const handleDeleteUser = async () => {
    if (!selectedUserId) return
    setUserDeleteSaving(true)
    try {
      await deleteUser(selectedUserId)
      const remaining = users.filter((u) => u.id !== selectedUserId)
      setUsers(remaining)
      setSelectedUserId(remaining[0]?.id)
      message.success('User deleted')
    } catch (e: any) {
      message.error(e?.message || 'Failed to delete user')
    } finally {
      setUserDeleteSaving(false)
    }
  }

  const handleResetPassword = async () => {
    if (!selectedUserId) return
    const newPassword = resetPasswordDraft.new_password.trim()
    const confirmPassword = resetPasswordDraft.confirm_password.trim()
    if (!newPassword) {
      message.warning('New password is required')
      return
    }
    if (newPassword !== confirmPassword) {
      message.warning('Passwords do not match')
      return
    }
    setResetPasswordSaving(true)
    try {
      await resetUserPassword(selectedUserId, {
        new_password: newPassword,
        confirm_password: confirmPassword,
      })
      setResetPasswordOpen(false)
      setResetPasswordDraft({ new_password: '', confirm_password: '' })
      message.success('Password reset')
    } catch (e: any) {
      const data = e?.response?.data
      const errText =
        data?.detail ||
        data?.message ||
        data?.new_password?.[0] ||
        data?.confirm_password?.[0] ||
        e?.message
      message.error(errText || 'Failed to reset password')
    } finally {
      setResetPasswordSaving(false)
    }
  }

  useEffect(() => {
    if (!selectedUserId) return
    loadUserEffectivePermissions(userGroupIds)
  }, [selectedUserId, userGroupIds, userPermissionIds])

  useEffect(() => {
    if (!selectedUserId || !selectedGroupId) return
    if (userGroupIds.includes(selectedGroupId)) {
      loadUserEffectivePermissions(userGroupIds)
    }
  }, [groupPermissionIds, selectedGroupId])

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(sortedUsers.length / userPageSize))
    if (userPage > maxPage) setUserPage(maxPage)
  }, [sortedUsers.length, userPage, userPageSize])

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key)}
        items={[
          {
            key: 'user',
            label: 'Users',
            children: (
              <Row gutter={12}>
                <Col span={7}>
                  <Card size="small">
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Input
                        placeholder="Search users"
                        value={userSearch}
                        onChange={(e) => setUserSearch(e.target.value)}
                      />
                      <Button type="primary" icon={<PlusCircleOutlined />} onClick={() => setCreateUserOpen(true)}>
                        Create user
                      </Button>
                      <List
                        size="small"
                        bordered
                        dataSource={sortedUsers}
                        pagination={{
                          current: userPage,
                          pageSize: userPageSize,
                          showSizeChanger: true,
                          pageSizeOptions: ['10', '20', '50'],
                          size: 'small',
                          onChange: (page, pageSize) => {
                            setUserPage(page)
                            setUserPageSize(pageSize)
                          },
                        }}
                        locale={{ emptyText: 'No users found' }}
                        renderItem={(u) => (
                          <List.Item
                            onClick={() => setSelectedUserId(u.id)}
                            style={{
                              cursor: 'pointer',
                              background: u.id === selectedUserId ? 'var(--access-list-selected-bg, #f0f6ff)' : undefined,
                            }}
                          >
                            <Space>
                              <Typography.Text strong={u.id === selectedUserId}>{u.username}</Typography.Text>
                              {u.is_superuser ? <Tag color="red">Superuser</Tag> : null}
                              {!u.is_superuser && u.is_staff ? <Tag color="blue">Staff</Tag> : null}
                            </Space>
                          </List.Item>
                        )}
                        style={{ maxHeight: 420, overflow: 'auto' }}
                      />
                    </Space>
                  </Card>
                </Col>
                <Col span={17}>
                  {selectedUserId ? (
                    <Card size="small">
                      <Tabs
                        size="small"
                        items={[
                          {
                            key: 'profile',
                            label: 'Profile',
                            children: (
                              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                                <Row gutter={10}>
                                  <Col span={12}>
                                    <Input
                                      placeholder="Username"
                                      value={userDraft.username}
                                      onChange={(e) => setUserDraft((prev) => ({ ...prev, username: e.target.value }))}
                                    />
                                  </Col>
                                  <Col span={12}>
                                    <Input
                                      placeholder="Email"
                                      value={userDraft.email}
                                      onChange={(e) => setUserDraft((prev) => ({ ...prev, email: e.target.value }))}
                                    />
                                  </Col>
                                </Row>
                                <Row gutter={10}>
                                  <Col span={12}>
                                    <Input
                                      placeholder="First name"
                                      value={userDraft.first_name}
                                      onChange={(e) => setUserDraft((prev) => ({ ...prev, first_name: e.target.value }))}
                                    />
                                  </Col>
                                  <Col span={12}>
                                    <Input
                                      placeholder="Last name"
                                      value={userDraft.last_name}
                                      onChange={(e) => setUserDraft((prev) => ({ ...prev, last_name: e.target.value }))}
                                    />
                                  </Col>
                                </Row>
                                <Space wrap>
                                  <Space>
                                    <Typography.Text type="secondary">Active</Typography.Text>
                                    <Switch
                                      checked={userDraft.is_active}
                                      onChange={(v) => setUserDraft((prev) => ({ ...prev, is_active: v }))}
                                    />
                                  </Space>
                                  <Space>
                                    <Typography.Text type="secondary">Staff</Typography.Text>
                                    <Switch
                                      checked={userDraft.is_staff}
                                      onChange={(v) => setUserDraft((prev) => ({ ...prev, is_staff: v }))}
                                    />
                                  </Space>
                                  <Space>
                                    <Typography.Text type="secondary">Superuser</Typography.Text>
                                    <Switch
                                      checked={userDraft.is_superuser}
                                      onChange={(v) => setUserDraft((prev) => ({ ...prev, is_superuser: v }))}
                                    />
                                  </Space>
                                </Space>
                                <Select
                                  mode="multiple"
                                  placeholder="User groups"
                                  value={userDraft.groups}
                                  onChange={(next) => setUserDraft((prev) => ({ ...prev, groups: next as number[] }))}
                                  style={{ width: '100%' }}
                                  options={groups.map((g) => ({ label: g.name, value: g.id }))}
                                />
                                <Space wrap>
                                  <Button type="primary" onClick={handleUpdateUser} loading={userSaving}>
                                    Save changes
                                  </Button>
                                  <Button
                                    onClick={() => {
                                      setResetPasswordDraft({ new_password: '', confirm_password: '' })
                                      setResetPasswordOpen(true)
                                    }}
                                    disabled={!selectedUserId}
                                  >
                                    Reset password
                                  </Button>
                                  <Button
                                    type="primary"
                                    onClick={setViewAsUser}
                                    disabled={
                                      !selectedUserId ||
                                      (!!currentUsername &&
                                        users.find((u) => u.id === selectedUserId)?.username === currentUsername)
                                    }
                                  >
                                    Impersonate
                                  </Button>
                                  <Popconfirm
                                    title="Delete this user?"
                                    onConfirm={handleDeleteUser}
                                    okText="Delete"
                                    cancelText="Cancel"
                                  >
                                    <Button danger loading={userDeleteSaving}>
                                      Delete user
                                    </Button>
                                  </Popconfirm>
                                </Space>
                              </Space>
                            ),
                          },
                          {
                            key: 'direct',
                            label: 'Direct permissions',
                            children: (
                              <Space direction="vertical" style={{ width: '100%' }} size={8}>
                                <Space style={{ marginBottom: 8 }} align="center" wrap>
                                  <Select
                                    value={userPermissionFilter}
                                    onChange={(v) => setUserPermissionFilter(v)}
                                    style={{ width: 220 }}
                                    options={permissionCategoryOptions}
                                    suffixIcon={<FilterOutlined />}
                                    allowClear
                                    placeholder="Category"
                                  />
                                  <Badge count={`${userCategoryAssignedCount}/${filteredUserPermissions.length}`} />
                                  <Popconfirm
                                    title={`Assign ${userCategoryLabel} permissions?`}
                                    onConfirm={() => applyUserCategory('assign')}
                                    okText="Assign"
                                    cancelText="Cancel"
                                    disabled={!selectedUserId}
                                    placement="top"
                                  >
                                    <Tooltip title="Assign all permissions in the selected category" placement="bottom">
                                      <Button icon={<PlusCircleOutlined />} type="primary" disabled={!selectedUserId}>
                                        Assign
                                      </Button>
                                    </Tooltip>
                                  </Popconfirm>
                                  <Popconfirm
                                    title={`Clear ${userCategoryLabel} permissions?`}
                                    onConfirm={() => applyUserCategory('clear')}
                                    okText="Clear"
                                    cancelText="Cancel"
                                    disabled={!selectedUserId}
                                    placement="top"
                                  >
                                    <Tooltip title="Remove all permissions in the selected category" placement="bottom">
                                      <Button icon={<DeleteOutlined />} danger disabled={!selectedUserId}>
                                        Clear
                                      </Button>
                                    </Tooltip>
                                  </Popconfirm>
                                </Space>
                                <Transfer
                                  dataSource={userPermissionTransferData}
                                  titles={['Available', 'Assigned']}
                                  filterOption={(inputValue, item) =>
                                    `${item.title} ${item.description}`.toLowerCase().includes(String(inputValue).toLowerCase())
                                  }
                                  targetKeys={userPermissionIds.map(String)}
                                  onChange={async (nextKeys) => {
                                    if (!selectedUserId) return
                                    const filteredIds = new Set(filteredUserPermissions.map((p) => p.id))
                                    const preserved = userPermissionIds.filter((id) => !filteredIds.has(id))
                                    const nextIds = [...preserved, ...nextKeys.map((k) => Number(k))]
                                    setUserPermissionIds(nextIds)
                                    try {
                                      await updateUserPermissions(selectedUserId, nextIds)
                                      message.success('User permissions updated')
                                      loadUserEffectivePermissions(userGroupIds)
                                    } catch (e: any) {
                                      message.error(e?.message || 'Failed to update user permissions')
                                    }
                                  }}
                                  render={(item) => (
                                    <div>
                                      <div>{item.title}</div>
                                      <div style={{ color: 'var(--access-secondary-text, #7a7a7a)' }}>{item.description}</div>
                                    </div>
                                  )}
                                  listStyle={{ width: 260, height: 320 }}
                                  showSearch
                                  disabled={!selectedUserId}
                                />
                              </Space>
                            ),
                          },
                          {
                            key: 'effective',
                            label: 'Effective permissions',
                            children: (
                              <Space direction="vertical" style={{ width: '100%' }} size={6}>
                                <Space wrap>
                                  <Tag color="blue">Direct: {directPermissionItems.length}</Tag>
                                  <Tag color="geekblue">From groups: {groupPermissionItems.length}</Tag>
                                  <Tag color="green">Effective: {effectivePermissionItems.length}</Tag>
                                </Space>
                                <List
                                  size="small"
                                  bordered
                                  dataSource={effectivePermissionItems}
                                  locale={{ emptyText: 'No effective permissions' }}
                                  renderItem={(p) => (
                                    <List.Item>
                                      <Space>
                                        <Typography.Text>{p.app_label}.{p.codename}</Typography.Text>
                                        <Typography.Text type="secondary">{p.name}</Typography.Text>
                                      </Space>
                                    </List.Item>
                                  )}
                                  style={{ maxHeight: 320, overflow: 'auto' }}
                                />
                              </Space>
                            ),
                          },
                        ]}
                      />
                    </Card>
                  ) : (
                    <Card size="small">
                      <Typography.Text type="secondary">Select a user to manage profile and permissions.</Typography.Text>
                    </Card>
                  )}
                </Col>
              </Row>
            ),
          },
          {
            key: 'groups',
            label: 'Groups',
            children: (
              <Row gutter={12}>
                <Col span={7}>
                  <Card size="small">
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Input
                        placeholder="Search groups"
                        value={groupSearch}
                        onChange={(e) => setGroupSearch(e.target.value)}
                      />
                      <Button type="primary" icon={<PlusCircleOutlined />} onClick={() => setCreateGroupOpen(true)}>
                        Create group
                      </Button>
                      <List
                        size="small"
                        bordered
                        dataSource={filteredGroups}
                        locale={{ emptyText: 'No groups found' }}
                        renderItem={(g) => (
                          <List.Item
                            onClick={() => setSelectedGroupId(g.id)}
                            style={{
                              cursor: 'pointer',
                              background: g.id === selectedGroupId ? 'var(--access-list-selected-bg, #f0f6ff)' : undefined,
                            }}
                          >
                            <Space>
                              <Typography.Text strong={g.id === selectedGroupId}>{g.name}</Typography.Text>
                              <Tooltip title="Members">
                                <Tag color="blue">{groupMemberCounts[g.id] || 0}</Tag>
                              </Tooltip>
                            </Space>
                          </List.Item>
                        )}
                        style={{ maxHeight: 420, overflow: 'auto' }}
                      />
                    </Space>
                  </Card>
                </Col>
                <Col span={17}>
                  {selectedGroupId ? (
                    <Card size="small">
                      <Tabs
                        size="small"
                        items={[
                          {
                            key: 'details',
                            label: 'Details',
                            children: (
                              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                                <Typography.Text type="secondary">Group name</Typography.Text>
                                <Input
                                  value={groupNameDraft}
                                  onChange={(e) => setGroupNameDraft(e.target.value)}
                                  placeholder="Group name"
                                />
                                <Space wrap>
                                  <Button type="primary" onClick={handleUpdateGroupName} loading={groupNameSaving}>
                                    Save changes
                                  </Button>
                                  <Popconfirm
                                    title="Delete this group?"
                                    onConfirm={handleDeleteGroup}
                                    okText="Delete"
                                    cancelText="Cancel"
                                  >
                                    <Button danger loading={groupDeleteSaving}>
                                      Delete group
                                    </Button>
                                  </Popconfirm>
                                </Space>
                              </Space>
                            ),
                          },
                          {
                            key: 'members',
                            label: 'Members',
                            children: (
                              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                                <Transfer
                                  dataSource={groupMemberTransferData}
                                  titles={['Available', 'Members']}
                                  targetKeys={groupMemberIds.map(String)}
                                  onChange={(nextKeys) => updateGroupMembers(nextKeys.map((k) => Number(k)))}
                                  render={(item) => (
                                    <div>
                                      <div>{item.title}</div>
                                      <div style={{ color: 'var(--access-secondary-text, #7a7a7a)' }}>{item.description}</div>
                                    </div>
                                  )}
                                  listStyle={{ width: 260, height: 320 }}
                                  showSearch
                                />
                                <Typography.Text type="secondary">
                                  Use bulk selection to add or remove multiple members.
                                </Typography.Text>
                              </Space>
                            ),
                          },
                          {
                            key: 'permissions',
                            label: 'Group permissions',
                            children: (
                              <Space direction="vertical" style={{ width: '100%' }} size={8}>
                                <Space style={{ marginBottom: 8 }} align="center" wrap>
                                  <Select
                                    value={groupPermissionFilter}
                                    onChange={(v) => setGroupPermissionFilter(v)}
                                    style={{ width: 220 }}
                                    options={permissionCategoryOptions}
                                    suffixIcon={<FilterOutlined />}
                                    allowClear
                                    placeholder="Category"
                                  />
                                  <Badge count={`${groupCategoryAssignedCount}/${filteredGroupPermissions.length}`} />
                                  <Popconfirm
                                    title={`Assign ${groupCategoryLabel} permissions?`}
                                    onConfirm={() => applyGroupCategory('assign')}
                                    okText="Assign"
                                    cancelText="Cancel"
                                    disabled={!selectedGroupId}
                                    placement="top"
                                  >
                                    <Tooltip title="Assign all permissions in the selected category" placement="bottom">
                                      <Button icon={<PlusCircleOutlined />} type="primary" disabled={!selectedGroupId}>
                                        Assign
                                      </Button>
                                    </Tooltip>
                                  </Popconfirm>
                                  <Popconfirm
                                    title={`Clear ${groupCategoryLabel} permissions?`}
                                    onConfirm={() => applyGroupCategory('clear')}
                                    okText="Clear"
                                    cancelText="Cancel"
                                    disabled={!selectedGroupId}
                                    placement="top"
                                  >
                                    <Tooltip title="Remove all permissions in the selected category" placement="bottom">
                                      <Button icon={<DeleteOutlined />} danger disabled={!selectedGroupId}>
                                        Clear
                                      </Button>
                                    </Tooltip>
                                  </Popconfirm>
                                </Space>
                                <Transfer
                                  dataSource={groupPermissionTransferData}
                                  titles={['Available', 'Assigned']}
                                  filterOption={(inputValue, item) =>
                                    `${item.title} ${item.description}`.toLowerCase().includes(String(inputValue).toLowerCase())
                                  }
                                  targetKeys={groupPermissionIds.map(String)}
                                  onChange={async (nextKeys) => {
                                    if (!selectedGroupId) return
                                    const filteredIds = new Set(filteredGroupPermissions.map((p) => p.id))
                                    const preserved = groupPermissionIds.filter((id) => !filteredIds.has(id))
                                    const nextIds = [...preserved, ...nextKeys.map((k) => Number(k))]
                                    setGroupPermissionIds(nextIds)
                                    try {
                                      await updateGroupPermissions(selectedGroupId, nextIds)
                                      message.success('Group permissions updated')
                                    } catch (e: any) {
                                      message.error(e?.message || 'Failed to update group permissions')
                                    }
                                  }}
                                  render={(item) => (
                                    <div>
                                      <div>{item.title}</div>
                                      <div style={{ color: 'var(--access-secondary-text, #7a7a7a)' }}>{item.description}</div>
                                    </div>
                                  )}
                                  listStyle={{ width: 260, height: 320 }}
                                  showSearch
                                  disabled={!selectedGroupId}
                                />
                              </Space>
                            ),
                          },
                        ]}
                      />
                    </Card>
                  ) : (
                    <Card size="small">
                      <Typography.Text type="secondary">Select a group to manage members, details, and permissions.</Typography.Text>
                    </Card>
                  )}
                </Col>
              </Row>
            ),
          },
        ]}
      />
      <Modal
        title="Create group"
        open={createGroupOpen}
        onCancel={() => setCreateGroupOpen(false)}
        onOk={handleCreateGroup}
        okText="Create"
        confirmLoading={createGroupSaving}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">Group name</Typography.Text>
          <Input
            placeholder="Group name"
            value={createGroupName}
            onChange={(e) => setCreateGroupName(e.target.value)}
          />
        </Space>
      </Modal>
      <Modal
        title="Reset password"
        open={resetPasswordOpen}
        onCancel={() => setResetPasswordOpen(false)}
        onOk={handleResetPassword}
        okText="Reset"
        confirmLoading={resetPasswordSaving}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">New password</Typography.Text>
          <Input.Password
            value={resetPasswordDraft.new_password}
            onChange={(e) => setResetPasswordDraft((prev) => ({ ...prev, new_password: e.target.value }))}
          />
          <Typography.Text type="secondary">Confirm password</Typography.Text>
          <Input.Password
            value={resetPasswordDraft.confirm_password}
            onChange={(e) => setResetPasswordDraft((prev) => ({ ...prev, confirm_password: e.target.value }))}
          />
        </Space>
      </Modal>
      <Modal
        title="Create user"
        open={createUserOpen}
        onCancel={() => setCreateUserOpen(false)}
        onOk={handleCreateUser}
        okText="Create"
        confirmLoading={createUserSaving}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input
            placeholder="Username"
            value={createUserDraft.username}
            onChange={(e) => setCreateUserDraft((prev) => ({ ...prev, username: e.target.value }))}
          />
          <Input
            placeholder="Email"
            value={createUserDraft.email}
            onChange={(e) => setCreateUserDraft((prev) => ({ ...prev, email: e.target.value }))}
          />
          <Row gutter={12}>
            <Col span={12}>
              <Input
                placeholder="First name"
                value={createUserDraft.first_name}
                onChange={(e) => setCreateUserDraft((prev) => ({ ...prev, first_name: e.target.value }))}
              />
            </Col>
            <Col span={12}>
              <Input
                placeholder="Last name"
                value={createUserDraft.last_name}
                onChange={(e) => setCreateUserDraft((prev) => ({ ...prev, last_name: e.target.value }))}
              />
            </Col>
          </Row>
          <Space wrap>
            <Space>
              <Typography.Text type="secondary">Active</Typography.Text>
              <Switch
                checked={createUserDraft.is_active}
                onChange={(v) => setCreateUserDraft((prev) => ({ ...prev, is_active: v }))}
              />
            </Space>
            <Space>
              <Typography.Text type="secondary">Staff</Typography.Text>
              <Switch
                checked={createUserDraft.is_staff}
                onChange={(v) => setCreateUserDraft((prev) => ({ ...prev, is_staff: v }))}
              />
            </Space>
            <Space>
              <Typography.Text type="secondary">Superuser</Typography.Text>
              <Switch
                checked={createUserDraft.is_superuser}
                onChange={(v) => setCreateUserDraft((prev) => ({ ...prev, is_superuser: v }))}
              />
            </Space>
          </Space>
          <Select
            mode="multiple"
            placeholder="Assign groups"
            value={createUserDraft.groups}
            onChange={(next) => setCreateUserDraft((prev) => ({ ...prev, groups: next as number[] }))}
            style={{ width: '100%' }}
            options={groups.map((g) => ({ label: g.name, value: g.id }))}
          />
        </Space>
      </Modal>
    </Space>
  )
}

export default Permissions

