<script setup lang="ts">
import { CheckmarkCircleOutline, AlertCircleOutline, InformationCircleOutline, WarningOutline, FlashOutline, RefreshOutline, TerminalOutline, DocumentTextOutline, SearchOutline, AddOutline } from '@vicons/ionicons5'
import StateBadge from '../../components/common/StateBadge.vue'
import type { LogPanelLine } from '../../components/common/LogPanel.vue'

const logLines = ref<LogPanelLine[]>([
  { id: 1, timestamp: '08:42:11', channel: 'kernel', level: 'info', text: 'plugin chat loaded successfully' },
  { id: 2, timestamp: '08:42:13', channel: 'llm', level: 'success', text: 'cache hit | tokens=2,438 latency=120ms' },
  { id: 3, timestamp: '08:43:02', channel: 'group', level: 'debug', text: 'debounce window opened | group=993065015' },
  { id: 4, timestamp: '08:43:05', channel: 'sticker', level: 'warning', text: 'silent learn failed | reason=image_download_timeout' },
  { id: 5, timestamp: '08:43:18', channel: 'compact', level: 'error', text: 'context compression failed | retry_in=60s' },
])

let logTick = logLines.value.length
const logInterval = ref<number | null>(null)
const logPaused = ref(false)

function pushLog() {
  if (logPaused.value) return
  logTick += 1
  logLines.value.push({
    id: logTick,
    timestamp: new Date().toLocaleTimeString('en-GB', { hour12: false }),
    channel: 'demo',
    level: (['info', 'debug', 'success', 'warning'] as const)[logTick % 4],
    text: `simulated log entry #${logTick}`,
  })
  if (logLines.value.length > 200) logLines.value = logLines.value.slice(-100)
}

onMounted(() => {
  logInterval.value = window.setInterval(pushLog, 1500)
})

onBeforeUnmount(() => {
  if (logInterval.value !== null) window.clearInterval(logInterval.value)
})

function toggleLogPause() {
  logPaused.value = !logPaused.value
}
function clearLog() {
  logLines.value = []
}

const tableData = ref([
  { id: 1, group: '984198159', name: '测试', status: 'success', updated: '2026-05-13 13:20' },
  { id: 2, group: '993065015', name: '烤', status: 'warning', updated: '2026-05-13 12:01' },
  { id: 3, group: '477640404', name: '大群', status: 'error', updated: '2026-05-13 09:45' },
])

const tableColumns = [
  { title: '群号', key: 'group' },
  { title: '群名', key: 'name' },
  {
    title: '状态',
    key: 'status',
    render(row: typeof tableData.value[number]) {
      const status = row.status as 'success' | 'warning' | 'error'
      const labels: Record<typeof status, string> = {
        success: '运行中',
        warning: '受限',
        error: '异常',
      }
      return h(StateBadge, { status, label: labels[status] })
    },
  },
  { title: '最近更新', key: 'updated' },
]

const filterText = ref('')
const filterTab = ref('all')

const fieldFormName = ref('凤笑梦')
const fieldFormSeconds = ref(5)
const fieldFormEnabled = ref(true)

const drawerVisible = ref(false)
</script>

<template>
  <AppPage
    eyebrow="Design System"
    title="设计系统验收"
    description="阶段 0-2 完成后的视觉验收页。打开浅/深主题切换，逐项核对组件渲染、间距、状态色、hover 与暗色模式可读性。"
  >
    <template #action>
      <NButton @click="drawerVisible = true">
        打开抽屉示例
      </NButton>
    </template>

    <!-- KPI metric cards -->
    <AppPanelSection
      eyebrow="MetricCard"
      title="KPI 指标卡"
      description="顶部状态区。accent 控制左上色条与图标色，分别为 primary / success / warning / info。"
    >
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-16">
        <MetricCard title="今日入库" :value="42" hint="较昨日 +5" :icon="FlashOutline" accent="primary" />
        <MetricCard title="缓存命中" value="86%" hint="健康" :icon="CheckmarkCircleOutline" accent="success" />
        <MetricCard title="待审条目" :value="7" hint="需关注" :icon="WarningOutline" accent="warning" />
        <MetricCard title="API 调用" value="1.2k" hint="今日累计" :icon="InformationCircleOutline" accent="info" />
      </div>
    </AppPanelSection>

    <!-- StateBadge -->
    <AppPanelSection
      class="mt-16"
      eyebrow="StateBadge"
      title="状态徽章"
      description="success / warning / error / info / default 五档语义。可选 icon 或默认圆点。"
    >
      <div class="flex flex-wrap gap-12">
        <StateBadge status="success" label="运行中" :icon="CheckmarkCircleOutline" />
        <StateBadge status="warning" label="受限" :icon="WarningOutline" />
        <StateBadge status="error" label="异常" :icon="AlertCircleOutline" />
        <StateBadge status="info" label="说明" :icon="InformationCircleOutline" />
        <StateBadge status="default" label="未启用" />
        <StateBadge status="success" label="紧凑" compact />
        <StateBadge status="warning" label="紧凑" compact />
      </div>
    </AppPanelSection>

    <!-- DataToolbar + DataTable -->
    <AppPanelSection
      class="mt-16"
      eyebrow="DataToolbar + DataTable"
      title="数据表工具条"
      description="列表页上方的标准工具条：左侧摘要 / 中间筛选 / 右侧操作。表格列里使用 StateBadge 渲染状态列。"
    >
      <DataToolbar label="共" :count="tableData.length">
        <template #filters>
          <NInput v-model:value="filterText" placeholder="搜索群号或名称" clearable style="width: 220px">
            <template #prefix>
              <NIcon :component="SearchOutline" />
            </template>
          </NInput>
          <NTabs v-model:value="filterTab" type="segment" size="small">
            <NTab name="all">
              全部
            </NTab>
            <NTab name="active">
              启用中
            </NTab>
            <NTab name="paused">
              已暂停
            </NTab>
          </NTabs>
        </template>
        <template #actions>
          <NButton :icon="RefreshOutline">
            <template #icon>
              <NIcon :component="RefreshOutline" />
            </template>
            刷新
          </NButton>
          <NButton type="primary">
            <template #icon>
              <NIcon :component="AddOutline" />
            </template>
            新增
          </NButton>
        </template>
      </DataToolbar>
      <NDataTable class="mt-12" :columns="tableColumns" :data="tableData" :bordered="false" />
    </AppPanelSection>

    <!-- LogPanel -->
    <AppPanelSection
      class="mt-16"
      eyebrow="LogPanel"
      title="日志面板"
      description="终端面板观感。等宽字体、行级 level 上色、可暂停/清屏、自动滚动到底。"
    >
      <LogPanel
        :lines="logLines"
        :paused="logPaused"
        title="实时日志（演示）"
        subtitle="每 1.5 秒自动追加一行；切到深色主题验证可读性"
        :icon="TerminalOutline"
        :height="280"
      >
        <template #actions>
          <NButton size="small" @click="toggleLogPause">
            {{ logPaused ? '继续' : '暂停' }}
          </NButton>
          <NButton size="small" @click="clearLog">
            清屏
          </NButton>
        </template>
        <template #footer>
          <span>{{ logLines.length }} 条 · {{ logPaused ? '已停止接收' : '实时追加中' }}</span>
        </template>
      </LogPanel>
    </AppPanelSection>

    <!-- FieldGroup -->
    <AppPanelSection
      class="mt-16"
      eyebrow="FieldGroup"
      title="表单字段分组"
      description="抽屉/表单里的字段单元，含标题 / 必填标记 / 帮助文字 / 右侧辅助槽。inline 模式标题左侧固定 140px。"
    >
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-24">
        <FieldGroup label="Bot 名称" required helper="作为消息发送方在群里展示的名字。修改后下次启动生效。">
          <NInput v-model:value="fieldFormName" placeholder="请输入" />
        </FieldGroup>
        <FieldGroup label="去抖时长" helper="收到首条消息后的等待秒数，期间累积消息合并处理。">
          <template #aside>
            <span>5–60 秒</span>
          </template>
          <NInputNumber v-model:value="fieldFormSeconds" :min="5" :max="60" />
        </FieldGroup>
        <FieldGroup inline label="启用插件" helper="关闭后该群的所有插件不再触发。">
          <NSwitch v-model:value="fieldFormEnabled" />
        </FieldGroup>
        <FieldGroup label="备注">
          <NInput type="textarea" :rows="2" placeholder="留空则不显示。" />
        </FieldGroup>
      </div>
    </AppPanelSection>

    <!-- Empty state -->
    <AppPanelSection
      class="mt-16"
      eyebrow="EmptyState"
      title="空状态"
      description="列表 / 抽屉 / 搜索结果为空时的统一形态。支持 icon / title / description / 引导操作槽。"
    >
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-16">
        <AppCard bordered>
          <EmptyState :icon="DocumentTextOutline" title="还没有记录" description="启用插件后这里会出现今日处理日志。">
            <NButton type="primary">
              去配置
            </NButton>
          </EmptyState>
        </AppCard>
        <AppCard bordered>
          <EmptyState :icon="SearchOutline" title="没有匹配项" description="试着调整搜索关键词或筛选条件。" compact />
        </AppCard>
      </div>
    </AppPanelSection>

    <!-- Tag / Button / Color contrast -->
    <AppPanelSection
      class="mt-16"
      eyebrow="Naive UI"
      title="基础控件主题校验"
      description="按钮 / 标签 / 文本色阶在浅深主题下的对比度。"
    >
      <div class="flex flex-wrap gap-12">
        <NButton>默认按钮</NButton>
        <NButton type="primary">
          主操作
        </NButton>
        <NButton type="info">
          信息
        </NButton>
        <NButton type="success">
          成功
        </NButton>
        <NButton type="warning">
          警告
        </NButton>
        <NButton type="error">
          危险
        </NButton>
        <NButton ghost>
          幽灵
        </NButton>
        <NButton text>
          文字
        </NButton>
      </div>
      <div class="mt-12 flex flex-wrap gap-8">
        <NTag>默认</NTag>
        <NTag type="success">
          成功
        </NTag>
        <NTag type="warning">
          警告
        </NTag>
        <NTag type="error">
          危险
        </NTag>
        <NTag type="info">
          信息
        </NTag>
        <NTag :bordered="true">
          描边
        </NTag>
      </div>
      <div class="mt-12 flex flex-col gap-8">
        <p class="text-[var(--om-text-1)]">
          一级文字 <span class="muted-text">— 用 var(--om-text-1)</span>
        </p>
        <p class="text-[var(--om-text-2)]">
          二级文字 <span class="muted-text">— 用 var(--om-text-2)</span>
        </p>
        <p class="text-[var(--om-text-3)]">
          三级文字 <span class="muted-text">— 用 var(--om-text-3)</span>
        </p>
      </div>
    </AppPanelSection>

    <!-- Drawer demo -->
    <NDrawer v-model:show="drawerVisible" :width="520">
      <NDrawerContent title="抽屉示例" closable>
        <AppDrawerLayout>
          <FieldGroup label="字段 A" helper="抽屉内字段示例。">
            <NInput placeholder="..." />
          </FieldGroup>
          <FieldGroup label="字段 B">
            <NSelect :options="[{ label: '选项一', value: 'a' }, { label: '选项二', value: 'b' }]" />
          </FieldGroup>
          <template #footer>
            <NButton @click="drawerVisible = false">
              取消
            </NButton>
            <NButton type="primary" @click="drawerVisible = false">
              保存
            </NButton>
          </template>
        </AppDrawerLayout>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.muted-text {
  color: var(--om-text-3);
  font-size: 12px;
}
</style>
