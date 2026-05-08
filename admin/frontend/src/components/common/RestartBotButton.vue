<script setup lang="ts">
import { PowerOutline } from '@vicons/ionicons5'
import { useDialog, useMessage } from 'naive-ui'
import { h } from 'vue'

import { api } from '../../api/client'

withDefaults(defineProps<{
  label?: string
}>(), {
  label: '在线重启',
})

const restarting = ref(false)
const dialog = useDialog()
const message = useMessage()

interface RestartNotice {
  title?: string
  summary?: string
  impact?: string[]
  works_for?: string[]
  needs_rebuild?: string[]
  checklist?: string[]
  window_hint?: string
}

function buildDialogContent(notice?: RestartNotice | null) {
  const summary = notice?.summary || '这个按钮只会重启当前 Bot 进程，让配置与运行态重新收敛；它不会重建镜像。'
  const impact = notice?.impact?.length
    ? notice.impact
    : [
        'QQ 连接会短暂中断，这段时间内群消息不会被当前进程处理。',
        '主动任务、定时链路与后台扫描会在进程恢复后继续运行。',
      ]
  const worksFor = notice?.works_for?.length
    ? notice.works_for
    : [
        '修改配置文件、Provider 映射、插件启停或其他运行参数后，可直接用在线重启让进程重新加载。',
      ]
  const needsRebuild = notice?.needs_rebuild?.length
    ? notice.needs_rebuild
    : [
        '修改 Python 代码、依赖或 Dockerfile 后，在线重启不会更新容器内代码，需要先重建 bot 镜像。',
      ]
  const checklist = notice?.checklist?.length
    ? notice.checklist
    : [
        '优先在群聊低峰期执行。',
        '确认最近备份或配置快照可用。',
        '若非 Docker 自动拉起环境，请提前确认手工启动方式。',
      ]
  const windowHint = notice?.window_hint || '改配置可直接在线重启；改代码或依赖请先重建镜像。'

  return h('div', { class: 'restart-dialog' }, [
    h('p', { class: 'restart-dialog__summary' }, summary),
    h('div', { class: 'restart-dialog__section' }, [
      h('strong', '影响范围'),
      h('ul', impact.map(item => h('li', item))),
    ]),
    h('div', { class: 'restart-dialog__section' }, [
      h('strong', '适合在线重启'),
      h('ul', worksFor.map(item => h('li', item))),
    ]),
    h('div', { class: 'restart-dialog__section' }, [
      h('strong', '需要先重建镜像'),
      h('ul', needsRebuild.map(item => h('li', item))),
    ]),
    h('div', { class: 'restart-dialog__section' }, [
      h('strong', '执行前确认'),
      h('ul', checklist.map(item => h('li', item))),
    ]),
    h('p', { class: 'restart-dialog__hint' }, windowHint),
  ])
}

async function handleRestart() {
  let notice: RestartNotice | null = null
  try {
    const data = await api<{ restart_notice?: RestartNotice }>('/api/admin/system')
    notice = data?.restart_notice || null
  } catch {
    notice = null
  }

  dialog.warning({
    title: notice?.title || '在线重启说明',
    content: () => buildDialogContent(notice),
    positiveText: '立即在线重启',
    negativeText: '取消',
    onPositiveClick: async () => {
      restarting.value = true
      try {
        const data = await api<{ ok: boolean, error?: string, message?: string }>('/api/admin/system/restart', {
          method: 'POST',
        })
        if (!data.ok) {
          message.error(data.error || '重启请求失败')
          return
        }
        message.success(data.message || '已发送重启请求')
      } catch (error) {
        console.error('Failed to restart bot:', error)
        const status = Number((error as any)?.response?.status || (error as any)?.statusCode || 0)
        if (status === 404) {
          message.error('当前运行中的 Bot 不支持在线重启接口，请先重建并重新拉起 bot 容器。')
        } else if (status === 401) {
          message.error('登录状态已失效，请刷新页面后重新登录。')
        } else {
          message.error('在线重启请求失败')
        }
      } finally {
        restarting.value = false
      }
    },
  })
}
</script>

<template>
  <NButton type="warning" secondary :loading="restarting" @click="handleRestart">
    <template #icon>
      <NIcon :component="PowerOutline" />
    </template>
    {{ label }}
  </NButton>
</template>

<style scoped>
.restart-dialog {
  display: grid;
  gap: 12px;
  color: var(--om-text-2, #607078);
  font-size: 13px;
  line-height: 1.7;
}

.restart-dialog__summary,
.restart-dialog__hint {
  margin: 0;
}

.restart-dialog__section {
  display: grid;
  gap: 6px;
}

.restart-dialog__section strong {
  color: var(--om-text-1, #1f2a30);
  font-size: 13px;
}

.restart-dialog__section ul {
  margin: 0;
  padding-left: 18px;
}

.restart-dialog__section li + li {
  margin-top: 4px;
}

.restart-dialog__hint {
  color: var(--om-text-3, #7a8b93);
}
</style>
