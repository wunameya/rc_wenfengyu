import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

const STATUS = {
  PENDING: { label: '等待投递', tone: 'neutral' },
  PROCESSING: { label: '投递中', tone: 'info' },
  RETRY_WAIT: { label: '等待重试', tone: 'warning' },
  SUCCEEDED: { label: '已成功', tone: 'success' },
  DEAD: { label: '已停止', tone: 'danger' },
}

const formatTime = (value) => {
  if (!value) return '—'
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  }).format(new Date(normalized))
}

const formatJson = (value) => JSON.stringify(value, null, 2)

async function api(path, options) {
  const response = await fetch(path, options)
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      message = body.detail || message
    } catch { /* 保留默认错误 */ }
    throw new Error(message)
  }
  return response.json()
}

function StatusBadge({ value }) {
  const status = STATUS[value] || { label: value, tone: 'neutral' }
  return <span className={`status status--${status.tone}`}><i />{status.label}</span>
}

function SummaryCard({ label, value, hint, tone = '' }) {
  return (
    <div className={`summary-card ${tone ? `summary-card--${tone}` : ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{hint}</small>
    </div>
  )
}

function JsonBlock({ title, value }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      <pre>{formatJson(value)}</pre>
    </section>
  )
}

function TaskDetailPanel({ taskId, onClose, onRetried }) {
  const [task, setTask] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retrying, setRetrying] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    api(`/api/v1/tasks/${taskId}`)
      .then((data) => active && setTask(data))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false))
    return () => { active = false }
  }, [taskId])

  const retry = async () => {
    setRetrying(true)
    setError('')
    try {
      await api(`/api/v1/tasks/${taskId}/retry`, { method: 'POST' })
      onRetried()
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className="drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="drawer" aria-label="任务详情">
        <header className="drawer-header">
          <div><span className="eyebrow">DELIVERY DETAIL</span><h2>投递详情</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        {loading && <div className="drawer-state">正在读取投递记录…</div>}
        {error && <div className="alert">{error}</div>}
        {task && (
          <div className="drawer-content">
            <div className="detail-lead">
              <StatusBadge value={task.status} />
              <code>{task.id}</code>
              {(task.status === 'DEAD' || task.status === 'RETRY_WAIT') && (
                <button className="primary-button" onClick={retry} disabled={retrying}>
                  {retrying ? '提交中…' : '立即重试'}
                </button>
              )}
            </div>
            <dl className="facts">
              <div><dt>渠道</dt><dd>{task.channel}</dd></div>
              <div><dt>幂等键</dt><dd>{task.idempotency_key}</dd></div>
              <div><dt>目标地址</dt><dd className="breakable">{task.request_method} {task.target_url}</dd></div>
              <div><dt>投递次数</dt><dd>{task.total_attempts} 次（本轮 {task.current_attempt}/{task.max_attempts}）</dd></div>
              <div><dt>创建时间</dt><dd>{formatTime(task.created_at)}</dd></div>
              <div><dt>下次重试</dt><dd>{task.status === 'RETRY_WAIT' ? formatTime(task.next_retry_at) : '—'}</dd></div>
              <div><dt>最近状态码</dt><dd>{task.last_http_status ?? '—'}</dd></div>
              <div><dt>最近错误</dt><dd className="error-text">{task.last_error || '—'}</dd></div>
            </dl>
            <JsonBlock title="请求 Header（敏感字段已脱敏）" value={task.request_headers} />
            <JsonBlock title="请求 Body" value={task.request_body} />
            <JsonBlock title="原始业务变量" value={task.variables} />
            <section className="detail-section">
              <h3>投递历史 <span>{task.attempts.length}</span></h3>
              {task.attempts.length === 0 ? <div className="empty-inline">尚未执行投递</div> : (
                <div className="timeline">
                  {[...task.attempts].reverse().map((attempt) => (
                    <article className="attempt" key={attempt.id}>
                      <div className={`attempt-dot attempt-dot--${attempt.outcome.toLowerCase()}`} />
                      <div className="attempt-main">
                        <div className="attempt-head">
                          <strong>第 {attempt.attempt_number} 次 · {STATUS[attempt.outcome]?.label || attempt.outcome}</strong>
                          <span>{formatTime(attempt.finished_at)} · {attempt.duration_ms} ms</span>
                        </div>
                        <div className="attempt-meta">HTTP {attempt.http_status ?? '—'} · {attempt.error || '请求成功'}</div>
                        {attempt.response_excerpt && <pre>{attempt.response_excerpt}</pre>}
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </aside>
    </div>
  )
}

function App() {
  const [tasks, setTasks] = useState([])
  const [summary, setSummary] = useState(null)
  const [channels, setChannels] = useState([])
  const [statuses, setStatuses] = useState(['RETRY_WAIT', 'DEAD'])
  const [channel, setChannel] = useState('')
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [selectedId, setSelectedId] = useState(
    () => new URLSearchParams(window.location.search).get('task')
  )
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshedAt, setRefreshedAt] = useState(null)

  const params = useMemo(() => {
    const search = new URLSearchParams()
    statuses.forEach((value) => search.append('status', value))
    if (channel) search.set('channel', channel)
    if (submittedQuery) search.set('q', submittedQuery)
    search.set('page_size', '50')
    return search.toString()
  }, [statuses, channel, submittedQuery])

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError('')
    try {
      const [taskData, summaryData] = await Promise.all([
        api(`/api/v1/tasks?${params}`),
        api('/api/v1/dashboard/summary'),
      ])
      setTasks(taskData.items)
      setTotal(taskData.total)
      setSummary(summaryData)
      setRefreshedAt(new Date())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [params])

  useEffect(() => {
    api('/api/v1/channels').then((data) => setChannels(data.items)).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(() => refresh(true), 15000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const toggleStatus = (value) => {
    setStatuses((current) => current.includes(value)
      ? current.filter((item) => item !== value)
      : [...current, value])
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark">N</div>
        <div><strong>Notify Relay</strong><span>投递控制台</span></div>
        <div className="service-state"><i /> 服务运行中</div>
      </header>

      <main>
        <section className="hero">
          <div><span className="eyebrow">OPERATIONS / DELIVERY</span><h1>失败投递</h1><p>集中查看、定位并恢复未成功送达的外部 HTTP 通知。</p></div>
          <button className="refresh-button" onClick={() => refresh()} disabled={loading}>
            <span>↻</span> {loading ? '刷新中' : '刷新'}
          </button>
        </section>

        <section className="summary-grid">
          <SummaryCard label="等待重试" value={summary?.retry_wait ?? '—'} hint="系统将自动再次投递" tone="warning" />
          <SummaryCard label="已停止" value={summary?.dead ?? '—'} hint="需要检查或人工重试" tone="danger" />
          <SummaryCard label="正在投递" value={summary?.processing ?? '—'} hint="Worker 正在处理" tone="info" />
          <SummaryCard label="24h 成功率" value={summary?.success_rate_24h == null ? '—' : `${summary.success_rate_24h}%`} hint={`${summary?.failed_attempts_24h ?? 0} 次失败尝试`} />
        </section>

        <section className="workspace">
          <div className="filters">
            <div className="segmented">
              {['RETRY_WAIT', 'DEAD', 'PROCESSING', 'SUCCEEDED'].map((value) => (
                <button key={value} className={statuses.includes(value) ? 'active' : ''} onClick={() => toggleStatus(value)}>
                  {STATUS[value].label}
                </button>
              ))}
            </div>
            <select value={channel} onChange={(event) => setChannel(event.target.value)} aria-label="渠道筛选">
              <option value="">全部渠道</option>
              {channels.map((item) => <option value={item} key={item}>{item}</option>)}
            </select>
            <form className="search" onSubmit={(event) => { event.preventDefault(); setSubmittedQuery(query.trim()) }}>
              <span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索任务 ID、幂等键或错误" />
            </form>
          </div>

          {error && <div className="alert">无法加载数据：{error}</div>}
          <div className="table-meta"><strong>{total}</strong> 条记录 <span>·</span> {refreshedAt ? `${refreshedAt.toLocaleTimeString('zh-CN')} 更新` : '尚未更新'}</div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>状态</th><th>任务 / 渠道</th><th>目标请求</th><th>失败信息</th><th>尝试</th><th>更新时间</th><th /></tr></thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id} onClick={() => setSelectedId(task.id)}>
                    <td><StatusBadge value={task.status} /></td>
                    <td><strong className="cell-title">{task.channel}</strong><code className="cell-subtitle">{task.idempotency_key}</code></td>
                    <td><strong className="method">{task.request_method}</strong><span className="url">{task.target_url}</span></td>
                    <td><span className="http-code">{task.last_http_status ? `HTTP ${task.last_http_status}` : '无响应'}</span><span className="error-summary">{task.last_error || '—'}</span></td>
                    <td><strong>{task.total_attempts}</strong><span className="cell-subtitle">本轮 {task.current_attempt}/{task.max_attempts}</span></td>
                    <td>{formatTime(task.updated_at)}{task.status === 'RETRY_WAIT' && <span className="cell-subtitle">重试 {formatTime(task.next_retry_at)}</span>}</td>
                    <td><button className="row-action" aria-label="查看详情">›</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!loading && tasks.length === 0 && <div className="empty-state"><div>✓</div><h3>当前筛选下没有失败投递</h3><p>新的异常请求会自动出现在这里。</p></div>}
            {loading && tasks.length === 0 && <div className="empty-state"><div className="spinner" /><h3>正在加载投递记录</h3></div>}
          </div>
        </section>
      </main>
      {selectedId && <TaskDetailPanel taskId={selectedId} onClose={() => setSelectedId(null)} onRetried={() => refresh()} />}
    </div>
  )
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>)

