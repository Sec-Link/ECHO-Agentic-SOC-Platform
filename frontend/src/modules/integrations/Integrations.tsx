import React, { useEffect, useState } from 'react'
import { List, Button, Modal, Form, Input, Card, Space, Tag, message, Select, Divider } from 'antd'
import {
  testEsIntegration,
  testLogstashIntegration,
  testAirflowIntegration,
  listIntegrations,
  createIntegration,
  updateIntegration,
  deleteIntegration,
  testDatasource,
} from 'services/integrations'
// Integrations page manages data integrations (Elasticsearch, Logstash, Airflow, PostgreSQL, MySQL).
// Key features:
// - List existing integrations
// - Create/edit integrations via form
// - Test connections and refresh target DB table lists
// - Preview ES index mapping, edit column names/types, and create tables from mapping
// Notes:
// - Edited columns are stored in editedColumns. After table creation, new integrations store columns in pendingMapping,
//   which are persisted to integration.config.columns on save. Existing integrations attempt to update config directly.
// - This file only handles UI-level data collection and backend API calls.
const Integrations: React.FC = () =>{
  const [items, setItems] = useState<any[]>([])
  const [showModal, setShowModal] = useState(false)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [form] = Form.useForm()

  useEffect(()=>{ fetchList() }, [])

  const fetchList = async ()=>{
    try{ const r = await listIntegrations(); setItems(r) }catch(e){ setItems([]) }
  }

  const testIntegration = async (info: any) => {
    const type = info.type || 'elasticsearch'
    if(type === 'elasticsearch'){
      // Normalize payload: prefer explicit host/url, fall back to config.host
      const host = info.host || info.url || (info.config && info.config.host) || ''
      const username = info.username || info.user || (info.config && info.config.username) || ''
      const password = info.password || (info.config && info.config.password) || ''
      const path = info.path || (info.config && info.config.path) || '/_cluster/health'
      if(!host) throw new Error('Elasticsearch host required')
      return testEsIntegration({ host, username, password, path })
    }
    if(type === 'logstash') return testLogstashIntegration(info)
    if(type === 'airflow') return testAirflowIntegration(info)
    if(type === 'postgresql' || type === 'mysql'){
      const payload: any = { db_type: type === 'postgresql' ? 'postgres' : 'mysql' }
      payload.user = info.user || info.username || ''
      payload.password = info.password || ''
      payload.host = info.host || ''
      payload.port = info.port || ''
      payload.database = info.dbname || info.database || info.db || ''
      return testDatasource(payload)
    }
    throw new Error('Unsupported integration type')
  }

  const save = async ()=>{
    const v = await form.validateFields()
    try{
      // Collect form values and build create/update integration payload.
      const payload: any = { name: v.name, type: v.type, config: {} }
      if(v.type === 'elasticsearch'){
        payload.config = { host: v.host || '', username: v.username || '', password: v.password || '' }
      }else if(v.type === 'logstash'){
        payload.config = v.config || { inputs: [], filters: [], outputs: [] }
      }else if(v.type === 'airflow'){
        payload.config = { host: v.host || '', username: v.username || '', password: v.password || '', token: v.token || '', path: v.path || '' }
      }else if(v.type === 'postgresql' || v.type === 'mysql'){
        payload.config = {
          conn_str: v.conn_str || undefined,
          host: v.host || undefined,
          port: v.port || undefined,
          user: v.user || v.username || undefined,
          password: v.password || undefined,
          dbname: v.dbname || v.database || undefined,
          django_db: v.django_db || undefined,
          table: v.table || undefined,
        }
      }else{
        payload.config = { ...(v.config || {}), host: v.host }
      }

      // Create vs update: editingIndex === null => create new integration; otherwise update existing
      if(editingIndex === null){
        await createIntegration(payload)
        message.success('Integration created')
      }else{
        const id = items[editingIndex].id
        await updateIntegration(id, payload)
        message.success('Integration updated')
      }
      setShowModal(false)
      setEditingIndex(null)
      form.resetFields()
      fetchList()
    }catch(e:any){ message.error(String(e)) }
  }

  const handleTestFromModal = async ()=>{
    try{
      const v = form.getFieldsValue()
      await testIntegration(v)
      message.success('Connection OK')
    }catch(e:any){ message.error('Connection failed: ' + (e.message || String(e))) }
  }

  const openNew = ()=>{ setEditingIndex(null); form.resetFields(); setShowModal(true) }

  const openEdit = (it:any, idx:number)=>{
    setEditingIndex(idx)
    const copy = { ...it }
    if(!copy.config) copy.config = { inputs: [], filters: [], outputs: [] }
    const merged: any = { ...copy }
    if(copy.type === 'logstash') merged.config = copy.config
    else{
      merged.host = copy.config.host || copy.config.url || undefined
      merged.username = copy.config.username
      merged.password = copy.config.password
      merged.token = copy.config.token
      merged.path = copy.config.path
      merged.conn_str = copy.config.conn_str || copy.config.url || undefined
      merged.port = copy.config.port || undefined
      merged.user = copy.config.user || copy.config.username || undefined
      merged.dbname = copy.config.dbname || copy.config.database || undefined
      merged.django_db = copy.config.django_db || undefined
      merged.table = copy.config.table || undefined
    }
    form.setFieldsValue(merged)
    setShowModal(true)
  }

  return (
    <div style={{ padding: 12 }}>
      <Card title="Integrations">
        <Button type="primary" onClick={openNew} style={{ marginBottom: 12 }}>Add Integration</Button>
        <List dataSource={items} renderItem={(it:any, idx)=> (
          <List.Item actions={[
            <Button key="view" onClick={()=>{ Modal.info({ title: `Integration: ${it.name || it.host}`, content: (<pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(it, null, 2)}</pre>), width: 700 }) }}>View</Button>,
            <Button key="test" onClick={async ()=>{ try{ const res = await testIntegration(it); Modal.success({ title: 'Connection OK', content: (<pre style={{ whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(res, null, 2)}</pre>) }) }catch(e:any){ const detail = e.body || e.message || String(e); Modal.error({ title: 'Connection failed', content: (<pre style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto' }}>{detail}</pre>), width: 700 }) } }}>Test</Button>,
            <Button key="edit" onClick={()=>openEdit(it, idx)}>Edit</Button>,
            <Button key="del" danger onClick={()=>{ Modal.confirm({ title: 'Delete integration?', content: `Delete ${it.name || it.host}? This cannot be undone.`, onOk: async ()=>{ try{ await deleteIntegration(it.id); message.success('Deleted'); fetchList() }catch(e:any){ message.error(String(e)) } } }) }}>Delete</Button>
          ]}>
            <List.Item.Meta title={<a onClick={()=>openEdit(it, idx)}>{it.name || it.host}</a>} description={<div><Tag>{it.type}</Tag> {it.host}</div>} />
          </List.Item>
        )} />
      </Card>

      <Modal open={showModal} onCancel={()=>setShowModal(false)} onOk={save} title="Add Integration">
        <Form form={form} layout="vertical" initialValues={{ type: 'elasticsearch' }}>
          <Form.Item name="type" label="Type">
            <Select onChange={(v:any)=>{ form.setFieldsValue({ type: v }) }}>
              <Select.Option value="elasticsearch">Elasticsearch</Select.Option>
              <Select.Option value="logstash">Logstash</Select.Option>
              <Select.Option value="airflow">Airflow</Select.Option>
              <Select.Option value="postgresql">PostgreSQL</Select.Option>
              <Select.Option value="mysql">MySQL</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item shouldUpdate noStyle>
            {()=>{
              const t = form.getFieldValue('type')
              let hostLabel = 'Host'
              if(t === 'elasticsearch') hostLabel = 'Host (http://...)'
              else if(t === 'postgresql' || t === 'mysql') hostLabel = 'DB Host'
              else if(t === 'airflow') hostLabel = 'Host (http://...)'

              return (
                <>
                  <Form.Item name="host" label={hostLabel}><Input /></Form.Item>

                  {t === 'elasticsearch' && (
                    <>
                      <Form.Item name="username" label="Username (optional)"><Input /></Form.Item>
                      <Form.Item name="password" label="Password (optional)"><Input.Password /></Form.Item>
                    </>
                  )}

                  {t === 'logstash' && (
                    <>
                      <Form.Item label="Logstash Config (inputs/filters/outputs)">
                        <Divider />
                        <Form.List name={[ 'config', 'inputs' ]}>
                          {(fields, { add, remove }) => (
                            <div>
                              <h4>Inputs</h4>
                              {fields.map(f=> (
                                <div key={f.key} style={{ marginBottom: 8 }}>
                                  <Form.Item name={[f.name, 'type']} rules={[{ required: true }]} style={{ display: 'inline-block', width: '30%', marginRight: 8 }}>
                                    <Select>
                                      <Select.Option value="file">file</Select.Option>
                                      <Select.Option value="tcp">tcp</Select.Option>
                                      <Select.Option value="http">http</Select.Option>
                                    </Select>
                                  </Form.Item>
                                  <Form.Item name={[f.name, 'path']} style={{ display: 'inline-block', width: '60%' }}>
                                    <Input placeholder="path or host" />
                                  </Form.Item>
                                  <Button danger onClick={()=>remove(f.name)}>Remove</Button>
                                </div>
                              ))}
                              <Button onClick={()=>add({ type: 'file', path: '' })}>Add Input</Button>
                            </div>
                          )}
                        </Form.List>

                        <Form.List name={[ 'config', 'filters' ]}>
                          {(fields, { add, remove }) => (
                            <div>
                              <h4>Filters</h4>
                              {fields.map(f=> (
                                <div key={f.key} style={{ marginBottom: 8 }}>
                                  <Form.Item name={[f.name, 'type']} rules={[{ required: true }]} style={{ display: 'inline-block', width: '30%', marginRight: 8 }}>
                                    <Select>
                                      <Select.Option value="grok">grok</Select.Option>
                                      <Select.Option value="mutate">mutate</Select.Option>
                                    </Select>
                                  </Form.Item>
                                  <Form.Item name={[f.name, 'pattern']} style={{ display: 'inline-block', width: '60%' }}>
                                    <Input placeholder="pattern or config" />
                                  </Form.Item>
                                  <Button danger onClick={()=>remove(f.name)}>Remove</Button>
                                </div>
                              ))}
                              <Button onClick={()=>add({ type: 'grok', pattern: '' })}>Add Filter</Button>
                            </div>
                          )}
                        </Form.List>

                        <Form.List name={[ 'config', 'outputs' ]}>
                          {(fields, { add, remove }) => (
                            <div>
                              <h4>Outputs</h4>
                              {fields.map(f=> (
                                <div key={f.key} style={{ marginBottom: 8 }}>
                                  <Form.Item name={[f.name, 'type']} rules={[{ required: true }]} style={{ display: 'inline-block', width: '30%', marginRight: 8 }}>
                                    <Select>
                                      <Select.Option value="elasticsearch">elasticsearch</Select.Option>
                                      <Select.Option value="postgresql">postgresql</Select.Option>
                                    </Select>
                                  </Form.Item>
                                  <Form.Item name={[f.name, 'config']} style={{ display: 'inline-block', width: '60%' }}>
                                    <Input placeholder="config or host" />
                                  </Form.Item>
                                  <Button danger onClick={()=>remove(f.name)}>Remove</Button>
                                </div>
                              ))}
                              <Button onClick={()=>add({ type: 'elasticsearch', config: '' })}>Add Output</Button>
                            </div>
                          )}
                        </Form.List>

                        <Button style={{ marginTop: 8 }} onClick={()=>{
                          const vals = form.getFieldsValue()
                          const cfg = vals.config || {}
                          let txt = ''
                          const ins = cfg.inputs || []
                          ins.forEach((i:any)=>{ txt += `input { ${i.type} { ${i.path || ''} } }\n` })
                          const fil = cfg.filters || []
                          fil.forEach((f:any)=>{ txt += `filter { ${f.type} { ${f.pattern || ''} } }\n` })
                          const outs = cfg.outputs || []
                          outs.forEach((o:any)=>{ txt += `output { ${o.type} { ${o.config || ''} } }\n` })
                          Modal.info({ title: 'Logstash config preview', width: 700, content: (<pre style={{ whiteSpace: 'pre-wrap' }}>{txt}</pre>) })
                        }}>Preview Logstash Config</Button>
                      </Form.Item>
                    </>
                  )}

                  {t === 'airflow' && (
                    <>
                      <Form.Item name="username" label="Username (optional)"><Input /></Form.Item>
                      <Form.Item name="password" label="Password (optional)"><Input.Password /></Form.Item>
                      <Form.Item name="token" label="Bearer Token (optional)"><Input /></Form.Item>
                      <Form.Item name="path" label="API Path (optional)"><Input placeholder="e.g. /api/v1/health" /></Form.Item>
                    </>
                  )}

                  {(t === 'postgresql' || t === 'mysql') && (
                    <>
                      <Form.Item name="conn_str" label="Connection string (optional)"><Input placeholder="e.g. postgresql://user:pass@host:5432/dbname" /></Form.Item>
                      <Form.Item name="port" label="Port"><Input /></Form.Item>
                      <Form.Item name="user" label="User"><Input /></Form.Item>
                      <Form.Item name="password" label="Password"><Input.Password /></Form.Item>
                      <Form.Item name="dbname" label="Database"><Input /></Form.Item>
                      <Form.Item name="django_db" label="Django DB alias (optional)"><Input placeholder="e.g. default" /></Form.Item>
                    </>
                  )}
                </>
              )
            }}
          </Form.Item>
          <Form.Item name="notes" label="Notes"><Input.TextArea rows={3} /></Form.Item>
          <Form.Item>
            <Space>
              <Button onClick={handleTestFromModal}>Test Connection</Button>
              <Button type="primary" onClick={save}>Save</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      
    </div>
  )
}
export default Integrations;
