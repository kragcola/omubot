<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NButton, NDataTable, NEmpty, NForm, NFormItem, NInput, NModal, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'

import { api } from '../../api/client'

interface BirthdayMember {
  qq: string
  name: string
  birthday_mmdd: string
  groups: string[]
}

const members = ref<BirthdayMember[]>([])
const sentLog = ref<Record<string, string[]>>({})
const loading = ref(false)
const saving = ref(false)
const showAdd = ref(false)
const form = ref({ qq: '', name: '', birthday_mmdd: '', groups: '' })

const message = useMessage()

async function fetchMembers() {
  loading.value = true
  try {
    const [mRes, lRes] = await Promise.all([
      api('/api/admin/birthday/members'),
      api('/api/admin/birthday/log'),
    ])
    if (mRes.ok) members.value = mRes.members
    if (lRes.ok) sentLog.value = lRes.sent_log
  } catch (error) {
    console.error('Failed to load birthday config:', error)
    message.error('生日配置加载失败')
  } finally {
    loading.value = false
  }
}

async function addMember() {
  const qq = form.value.qq.trim()
  const birthday = form.value.birthday_mmdd.trim()
  if (!qq || !birthday) {
    message.warning('请填写 QQ 号和生日')
    return
  }
  const groups = form.value.groups.split(/[,，\s]+/).filter(Boolean)
  saving.value = true
  try {
    const data = await api('/api/admin/birthday/members', {
      method: 'POST',
      body: { qq, name: form.value.name.trim(), birthday_mmdd: birthday, groups },
    })
    if (!data.ok) {
      message.error(data.error || '添加失败')
      return
    }
    message.success('已添加')
    showAdd.value = false
    form.value = { qq: '', name: '', birthday_mmdd: '', groups: '' }
    await fetchMembers()
  } catch (error) {
    console.error('Failed to add birthday member:', error)
    message.error('添加失败')
  } finally {
    saving.value = false
  }
}

async function removeMember(qq: string) {
  try {
    const data = await api(`/api/admin/birthday/members/${qq}`, { method: 'DELETE' })
    if (!data.ok) {
      message.error(data.error || '删除失败')
      return
    }
    message.success('已删除')
    await fetchMembers()
  } catch (error) {
    console.error('Failed to remove birthday member:', error)
    message.error('删除失败')
  }
}

const columns: DataTableColumns<BirthdayMember> = [
  { title: 'QQ', key: 'qq', width: 140 },
  { title: '昵称', key: 'name', width: 120 },
  { title: '生日', key: 'birthday_mmdd', width: 80 },
  { title: '关联群', key: 'groups', render: (row) => row.groups.join(', ') },
  {
    title: '操作', key: 'actions', width: 80,
    render: (row) => h(NButton, { size: 'tiny', type: 'error', secondary: true, onClick: () => removeMember(row.qq) }, () => '删除'),
  },
]

onMounted(fetchMembers)
</script>

<template>
  <AppPage title="生日祝福" description="配置群友生日，当天自动 @祝福（每天仅一次）">
    <template #action>
      <NButton type="primary" size="small" @click="showAdd = true">添加成员</NButton>
    </template>

    <div class="birthday-view">
      <NDataTable
        v-if="members.length > 0"
        :columns="columns"
        :data="members"
        :loading="loading"
        :bordered="false"
        size="small"
        :row-key="(row: BirthdayMember) => row.qq"
      />
      <NEmpty v-else description="暂无配置，点击右上角添加" />

      <div v-if="Object.keys(sentLog).length > 0" class="birthday-view__log">
        <h4>发送记录</h4>
        <div v-for="(qqs, date) in sentLog" :key="date" class="birthday-view__log-row">
          <span class="birthday-view__log-date">{{ date }}</span>
          <span>{{ qqs.join(', ') }}</span>
        </div>
      </div>
    </div>

    <NModal v-model:show="showAdd" preset="card" title="添加生日成员" style="width: 420px">
      <NForm label-placement="left" label-width="70">
        <NFormItem label="QQ 号">
          <NInput v-model:value="form.qq" placeholder="如 123456789" />
        </NFormItem>
        <NFormItem label="昵称">
          <NInput v-model:value="form.name" placeholder="群内昵称" />
        </NFormItem>
        <NFormItem label="生日">
          <NInput v-model:value="form.birthday_mmdd" placeholder="MM-DD 如 03-15" />
        </NFormItem>
        <NFormItem label="群号">
          <NInput v-model:value="form.groups" placeholder="多个用逗号分隔" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NButton type="primary" :loading="saving" @click="addMember">确认添加</NButton>
      </template>
    </NModal>
  </AppPage>
</template>

<style scoped>
.birthday-view {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.birthday-view__log {
  padding: 16px;
  border-radius: 10px;
  background: var(--om-surface-2);
}

.birthday-view__log h4 {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--om-text-2);
}

.birthday-view__log-row {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--om-text-2);
  padding: 4px 0;
}

.birthday-view__log-date {
  color: var(--om-text-3);
  font-variant-numeric: tabular-nums;
}
</style>
