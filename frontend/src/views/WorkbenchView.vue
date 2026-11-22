<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <section v-if="meta.key === 'derived_attribution'" class="attribution-panel" data-testid="attribution-panel">
      <div class="attribution-bar">
        <label class="filter-item">
          <span>复核域</span>
          <input v-model="reviewDomain" data-testid="review-domain" placeholder="交易域 / 结算域" />
        </label>
        <label class="filter-item">
          <span>提交人</span>
          <input v-model="reviewSubmitter" data-testid="review-submitter" placeholder="提交人" />
        </label>
        <label class="filter-item">
          <span>轮次</span>
          <input v-model="reviewRound" data-testid="review-round" placeholder="2026-11" />
        </label>
        <button class="btn primary" data-testid="load-scope" type="button" @click="loadScope">
          读取可提交范围
        </button>
        <button class="btn" data-testid="load-report" type="button" @click="loadReport">
          刷新覆盖报告
        </button>
        <button class="btn" data-testid="load-revalidate" type="button" @click="loadRevalidate">
          复查结论与范围
        </button>
      </div>

      <div class="attribution-grid">
        <article class="panel-card">
          <h3>本域可提交字段</h3>
          <p class="panel-note" data-testid="scope-summary">{{ scopeSummary }}</p>
          <ul class="scope-list">
            <li v-for="item in scope?.entries ?? []" :key="item.清单编号" :data-testid="`scope-${item.目标字段}`">
              <strong>{{ item.目标字段 }}</strong>
              <span>{{ item.权限类型 }} · {{ item.归属域 }}</span>
              <em>{{ item.授权编号 || item.血缘依据 }}</em>
            </li>
          </ul>
        </article>

        <article class="panel-card">
          <h3>覆盖报告（按字段去重）</h3>
          <p class="panel-note" data-testid="report-summary">{{ reportSummary }}</p>
          <table class="data-table compact">
            <thead>
              <tr><th>字段</th><th>已覆盖</th><th>确认域</th><th>生效结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in report?.field_details ?? []" :key="item.目标字段">
                <td>{{ item.目标字段 }}</td>
                <td>{{ item.covered ? '是' : '否' }}</td>
                <td>{{ item.confirmed_domains.join('、') || '—' }}</td>
                <td>{{ item.effective_conclusion || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </article>
      </div>

      <p v-if="revalidation" class="panel-note" data-testid="revalidate-summary">{{ revalidateSummary }}</p>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import { fetchJson } from '@/api/client'
import type { EntryRow } from '@/data/types'

type ScopeEntry = {
  清单编号: string
  目标字段: string
  权限类型: string
  归属域: string
  授权编号: string
  血缘依据: string
}
type FieldScope = { domain: string; fields: string[]; count: number; denominator_fields: number; entries: ScopeEntry[] }
type FieldDetail = { 目标字段: string; covered: boolean; confirmed_domains: string[]; effective_conclusion: string }
type Report = { denominator_fields: number; covered_fields: number; coverage_rate: number; status: string; field_details: FieldDetail[] }
type Revalidate = { report_status: string; authorization_lapsed: { 目标字段: string; 提交域: string }[] }

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const reviewDomain = ref('交易域')
const reviewSubmitter = ref('交易-李四')
const reviewRound = ref('')
const scope = ref<FieldScope | null>(null)
const report = ref<Report | null>(null)
const revalidation = ref<Revalidate | null>(null)
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

const scopeSummary = computed(() =>
  scope.value
    ? `${scope.value.domain}可提交 ${scope.value.count} / ${scope.value.denominator_fields} 个字段`
    : '尚未读取可提交范围',
)
const reportSummary = computed(() =>
  report.value
    ? `已覆盖 ${report.value.covered_fields} / ${report.value.denominator_fields} 个字段，状态：${report.value.status}`
    : '尚未生成覆盖报告',
)
const revalidateSummary = computed(() => {
  if (!revalidation.value) return ''
  const lapsed = revalidation.value.authorization_lapsed
  const detail = lapsed.length
    ? `授权失效：${lapsed.map((item) => `${item.提交域}/${item.目标字段}`).join('、')}`
    : '已确认结论授权仍有效'
  return `复查结果：${revalidation.value.report_status}；${detail}`
})

function scopeQuery() {
  const params = new URLSearchParams({ domain: reviewDomain.value })
  return params.toString()
}

async function loadScope() {
  errorMessage.value = ''
  try {
    scope.value = await fetchJson<FieldScope>(`/api/attribution/fields?${scopeQuery()}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段范围读取失败'
  }
}

async function loadReport() {
  errorMessage.value = ''
  try {
    const params = reviewRound.value ? `?round_id=${encodeURIComponent(reviewRound.value)}` : ''
    report.value = await fetchJson<Report>(`/api/attribution/report${params}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '覆盖报告读取失败'
  }
}

async function loadRevalidate() {
  errorMessage.value = ''
  try {
    const round = reviewRound.value ? `&round_id=${encodeURIComponent(reviewRound.value)}` : ''
    const payload = await fetchJson<{ revalidation: Revalidate }>(`/api/attribution/revalidate?table=DWD_TRADE_SETTLE${round}`)
    revalidation.value = payload.revalidation
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复查失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(() => {
  void reload()
  if (meta.key === 'derived_attribution') {
    void loadScope()
    void loadReport()
  }
})
</script>
