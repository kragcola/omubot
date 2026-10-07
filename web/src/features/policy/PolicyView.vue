<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { NAlert, NButton, NCard, NCheckbox, NInput, NInputNumber, NSelect, NSpace, NTag, NText } from 'naive-ui'

import type { Grant, TaskBinding } from '@/api/types'
import ContactConsentPanel from './ContactConsentPanel.vue'
import {
  addBackgroundModelGrant,
  addVisibilityGovernanceDraft,
  canGrantVisibility,
  grantVisibility,
  loadVisibilityOptions,
  revokeVisibility,
  visibilityOptionKey,
  addPrivateConversationGrants,
  addDiagnosticGrant,
  addStickerAdministrationGrant,
  addFetchGrant,
  addHttpGrant,
  addSearchGrant,
  addUserGrants,
  cancelClearGrants,
  cancelDeleteGrant,
  cancelReload,
  canAddFetchGrant,
  canAddPrivateConversationGrants,
  canAddDiagnosticGrant,
  canAddHttpGrant,
  confirmClearGrants,
  confirmDeleteGrant,
  confirmReload,
  hasPolicyDraftChanges,
  loadPolicy,
  policyState,
  requestClearGrants,
  requestDeleteGrant,
  requestReload,
  savePolicy,
} from './store'

interface RuntimeDestination {
  task: string
  label: string
  binding: TaskBinding
}

const runtimeDestinations = computed<RuntimeDestination[]>(() => {
  const snapshot = policyState.snapshot
  if (!snapshot) return []
  const rows: Array<['reply' | 'thinker' | 'vision', string]> = [['reply', '普通回复']]
  if (snapshot.thinker_enabled) rows.push(['thinker', 'Thinker'])
  const seen = new Set<string>()
  const result: RuntimeDestination[] = []
  for (const [task, label] of rows) {
    const binding = snapshot.model_config.task_bindings[task]
    if (!binding) continue
    const key = `${binding.policy_provider}\u0000${binding.model}`
    if (seen.has(key)) continue
    seen.add(key)
    result.push({ task, label, binding })
  }
  return result
})

const modeLabel = computed(() => policyState.snapshot?.mode === 'live' ? '实时模式' : '离线模式')
const modeTagType = computed<'warning' | 'success' | 'default'>(() => policyState.snapshot?.mode === 'live' ? 'warning' : 'success')
const addDisabled = computed(() => policyState.saving || policyState.loading || !policyState.groupId.trim() || !policyState.userId.trim())
const searchDestinations = computed(() => (policyState.snapshot?.tool_destinations ?? [])
  .filter((item) => item.tool_id === 'web.search'))
const fetchToolAvailable = computed(() => policyState.snapshot?.request_destination_tools?.includes('web.fetch') ?? false)
const fetchGrantDisabled = computed(() => !canAddFetchGrant())
const httpGrantDisabled = computed(() => !canAddHttpGrant())
const httpToolAvailable = computed(() => policyState.snapshot?.request_destination_tools?.includes(
  policyState.httpMethod === 'GET' ? 'http.get' : 'http.post',
) ?? false)
const httpMethodOptions: Array<{ label: string; value: 'GET' | 'POST' }> = [
  { label: 'GET · 读取', value: 'GET' },
  { label: 'POST · 写入', value: 'POST' },
]
const visibilityDisabled = computed(() => !canGrantVisibility())
const visibilityOptions = computed(() => policyState.visibilityOptions.map((item) => ({
  label: `${item.label} · ${item.summary}`, value: visibilityOptionKey(item),
})))
const visibilityMaterialOptions = [
  {label: '知识文档', value: 'knowledge'},
  {label: '黑话解释', value: 'slang'},
  {label: '通用表达', value: 'style'},
]
const privateGrantDisabled = computed(() => !canAddPrivateConversationGrants())
const privateEnabled = computed(() => policyState.snapshot?.private_conversation_enabled === true)
const diagnosticGrantDisabled = computed(() => !canAddDiagnosticGrant())
const diagnosticEnabled = computed(() => policyState.snapshot?.diagnostic_commands_enabled === true)
const diagnosticGroupAllowed = computed(() => {
  const groups = policyState.snapshot?.diagnostic_commands_groups
  return Array.isArray(groups) && groups.includes(policyState.groupId.trim())
})

function grantScopeLabel(grant: Grant): string {
  return grant.scope.kind === 'private' ? `私聊 ${grant.scope.private_user_id}` : `群 ${grant.scope.group_id}`
}

function grantExpiry(grant: {expires_at: number}): string {
  return new Date(grant.expires_at * 1000).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

function grantActions(grant: Grant): string {
  return grant.actions.join('、')
}

function grantMediaCapabilities(grant: Grant): string {
  const capabilities: string[] = []
  if (grant.actions.includes('media.read')) capabilities.push('本轮图片读取')
  if (grant.allow_images) capabilities.push('模型图片上传')
  if (grant.actions.includes('message.sticker') && grant.actions.includes('media.send')) {
    capabilities.push('审核表情发送')
  }
  return capabilities.length ? capabilities.join('、') : '无媒体能力'
}

function addGrant(): void {
  addUserGrants()
}

async function saveChanges(): Promise<void> {
  await savePolicy()
}

onMounted(() => {
  void loadPolicy()
})
</script>

<template>
  <section class="policy-view" aria-label="权限策略">
    <n-alert v-if="policyState.error" class="notice" type="error" :show-icon="true">
      {{ policyState.error }}
    </n-alert>
    <n-alert v-if="policyState.notice" class="notice" type="info" :show-icon="true">
      {{ policyState.notice }}
    </n-alert>

    <n-card v-if="policyState.loading && !policyState.snapshot" class="page-card">
      <div class="empty-state">正在读取权限…</div>
    </n-card>

    <template v-if="policyState.snapshot && policyState.draft">
      <ContactConsentPanel :disabled="policyState.saving || policyState.loading || hasPolicyDraftChanges()" @updated="requestReload" />
      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">运行快照</p>
            <h2>授权使用的模型</h2>
            <p class="subtle-text">授权范围取自当前正在运行的任务绑定；保存配置后的待重启模型不会提前出现在这里。</p>
          </div>
          <n-space align="center" size="small">
            <n-tag :type="modeTagType" size="small">{{ modeLabel }}
            </n-tag>
            <n-tag type="info" size="small">版本 {{ policyState.snapshot.revision }}
            </n-tag>
            <n-button size="small" :loading="policyState.loading" :disabled="policyState.saving" @click="requestReload">重新读取
            </n-button>
          </n-space>
        </div>
        <div v-if="policyState.reloadPending" class="confirm-strip">
          <p>当前页面有未保存权限草稿。重新读取会放弃这些修改。</p>
          <div class="button-row">
            <n-button size="small" @click="cancelReload">继续编辑
            </n-button>
            <n-button size="small" type="warning" @click="confirmReload">放弃草稿并重新读取
            </n-button>
          </div>
        </div>
        <div class="info-grid">
          <div class="info-block">
            <div class="label">Bot ID</div>
            <div class="value">{{ policyState.snapshot.bot_id }}</div>
          </div>
          <div class="info-block">
            <div class="label">当前默认模型</div>
            <div class="value">{{ policyState.snapshot.model_config.profile }} · {{ policyState.snapshot.model_config.model }}</div>
          </div>
          <div class="info-block">
            <div class="label">接口格式</div>
            <div class="value">{{ policyState.snapshot.model_config.api_format }}</div>
          </div>
          <div class="info-block">
            <div class="label">Thinker</div>
            <div class="value">{{ policyState.snapshot.thinker_enabled ? '已启用' : '未启用' }}</div>
          </div>
        </div>
        <n-alert class="notice" type="warning" :show-icon="true">
          直接图片须通过显式域名白名单取得字节；容器路径不读取。当前视觉授权只记录能力，不会读取或上传图片像素。
        </n-alert>
        <div class="destination-grid">
          <div v-for="destination in runtimeDestinations" :key="destination.task" class="destination-card">
            <div class="destination-label">{{ destination.label }}</div>
            <strong>{{ destination.binding.profile }}</strong>
            <span>{{ destination.binding.model }}</span>
            <details><summary>授权标识</summary><small>{{ destination.binding.policy_provider }}</small></details>
          </div>
          <div v-if="!runtimeDestinations.length" class="empty-state">运行快照尚未提供可授权的回复模型。</div>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">新增授权</p>
            <h2>给明确的群和用户开放</h2>
            <p class="subtle-text">新实例默认没有授权。新增会加入草稿，点击“保存权限”后立即生效，无需重启；按当前运行任务模型生成精确授权。</p>
          </div>
          <n-tag type="default" size="small">最多 256 条</n-tag>
        </div>
        <form class="grant-form" @submit.prevent="addGrant">
          <div class="form-grid">
            <div class="form-field">
              <label class="field-label" for="grant-group-input">群 ID</label>
              <n-input id="grant-group" :input-props="{id: 'grant-group-input', 'aria-label': '群 ID'}" v-model:value="policyState.groupId" :disabled="policyState.saving || policyState.loading" placeholder="例如：test-group（离线）或真实群号" />
              <p class="form-hint">默认留空，必须明确填写。离线可用 test-group；真实使用填 QQ 群号，不支持通配授权。</p>
            </div>
            <div class="form-field">
              <label class="field-label" for="grant-user-input">用户 ID</label>
              <n-input id="grant-user" :input-props="{id: 'grant-user-input', 'aria-label': '用户 ID'}" v-model:value="policyState.userId" :disabled="policyState.saving || policyState.loading" placeholder="例如：test-user（离线）或真实 QQ 号" />
              <p class="form-hint">默认留空。填写此群内获准使用 Bot 的用户 QQ 号；离线可用 test-user，并在试聊填写同一标识。</p>
            </div>
            <div class="form-field">
              <label class="field-label" for="grant-expiry">有效期（小时）</label>
              <n-input-number id="grant-expiry" v-model:value="policyState.expiryHours" :min="0.1" :max="8760" :step="1" :disabled="policyState.saving || policyState.loading" />
              <p class="form-hint">默认 24 小时，可填 0.1–8760；从添加到草稿时起算，到期后自动失效。</p>
            </div>
            <div class="form-field toggle-field">
              <span class="field-label">模型上下文</span>
              <n-checkbox v-model:checked="policyState.allowHistory" :disabled="policyState.saving || policyState.loading">允许读取历史消息</n-checkbox>
              <p class="form-hint">默认开启。允许普通回复和 Thinker 使用该会话历史；模型配置也需开启发送历史。</p>
            </div>
            <div class="form-field form-field-wide">
              <span class="field-label">媒体能力（默认关闭）</span>
              <div class="toggle-grid">
                <div class="toggle-row">
                  <n-checkbox v-model:checked="policyState.allowCurrentImage" :disabled="policyState.saving || policyState.loading">
                    允许读取本轮当前图片
                  </n-checkbox>
                </div>
                <div class="toggle-row">
                  <n-checkbox v-model:checked="policyState.allowImageUpload" :disabled="policyState.saving || policyState.loading || !policyState.allowCurrentImage">
                    允许把当前图片上传给模型
                  </n-checkbox>
                </div>
                <div class="toggle-row">
                  <n-checkbox v-model:checked="policyState.allowStickerSend" :disabled="policyState.saving || policyState.loading">
                    允许发送审核表情
                  </n-checkbox>
                </div>
              </div>
              <p class="form-hint">模型图片上传必须同时允许本轮当前图片读取；这不会授予历史媒体读取。审核表情对应 message.sticker + media.send，表情库默认为空，候选缺失时回到短文字。</p>
              <div class="button-row">
                <n-button :disabled="policyState.saving || policyState.loading" @click="addStickerAdministrationGrant()">加入本群表情管理页许可</n-button>
                <n-button :disabled="policyState.saving || policyState.loading || !policyState.snapshot?.sticker_description_available" @click="addStickerAdministrationGrant(true)">加入本群管理页视觉描述许可</n-button>
              </div>
              <p class="form-hint">管理页主体为 web-admin。管理资源和上传给 vision 模型分别授权；此入口不会增加聊天发送、工具或历史上传许可。加入后仍需保存权限草稿。</p>
            </div>
            <div class="form-field form-field-wide">
              <span class="field-label">N6 记忆能力（各自默认关闭）</span>
              <div class="toggle-grid">
                <div class="toggle-row">
                  <n-checkbox v-model:checked="policyState.allowMemoryArchive" :disabled="policyState.saving || policyState.loading">
                    允许保存 memory.archive 来源身份
                  </n-checkbox>
                </div>
                <div class="toggle-row">
                  <n-checkbox v-model:checked="policyState.allowMemoryLearn" :disabled="policyState.saving || policyState.loading">
                    允许 memory.learn 候选写入
                  </n-checkbox>
                </div>
                <div class="toggle-row">
                  <n-checkbox v-model:checked="policyState.allowMemoryRetrieve" :disabled="policyState.saving || policyState.loading">
                    允许 memory.retrieve 读取
                  </n-checkbox>
                </div>
                <div class="toggle-row">
                  <n-checkbox v-model:checked="policyState.allowMemoryReview" :disabled="policyState.saving || policyState.loading">
                    允许 memory.review 审核
                  </n-checkbox>
                </div>
                <div class="toggle-row">
                  <n-checkbox v-model:checked="policyState.allowMemoryApply" :disabled="policyState.saving || policyState.loading">
                    允许 memory.apply 应用
                  </n-checkbox>
                </div>
              </div>
              <p class="form-hint">memory.archive 元数据只保存来源身份。仅当设置页显式开启 N6 采集、群 ID 命中白名单且作者与 Bot actor 都有同群 message.read、memory.archive、memory.learn 时，纯文字才会进入独立加密临时区（固定最长 24 小时）供 task_models.memory 抽取；默认关闭。普通采集排除 @、引用、图片、转发和卡片。显式开启本人称呼并指定群后，本人“叫我 X”及对 Bot 的可信 @ 可直接更新本群称呼，成功回复依赖真实写入与当前授权；替其他用户起外号仍需审核。限定低风险自动审核/应用可独立开启，实际回执及回退见记忆管理页；其余候选由有权限的管理员审核与应用。未知 QQ Bot 无可靠自动识别。</p>
            </div>
          </div>
          <div class="button-row">
            <n-button type="primary" attr-type="submit" :disabled="addDisabled">新增精确授权</n-button>
            <n-tag type="default" size="small">默认只授予文字能力；媒体能力需单独勾选</n-tag>
          </div>
        </form>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">私聊授权</p>
            <h2>给明确的私聊用户开放</h2>
            <p class="subtle-text">仅使用当前运行版本的私聊名单与回复模型。加入草稿后须保存权限；历史默认关闭，群资料不进入私聊。</p>
          </div>
          <n-tag :type="privateEnabled ? 'success' : 'default'" size="small">{{ privateEnabled ? '运行版已启用' : '运行版未启用' }}
          </n-tag>
        </div>
        <form class="grant-form" @submit.prevent="addPrivateConversationGrants">
          <div class="form-grid">
            <div class="form-field">
              <label class="field-label" for="private-peer-input">私聊用户 ID</label>
              <n-input :input-props="{id: 'private-peer-input', 'aria-label': '私聊用户 ID'}" v-model:value="policyState.privatePeerId" :disabled="policyState.saving || policyState.loading || !privateEnabled" placeholder="运行版名单中的精确用户 ID" />
            </div>
            <div class="form-field">
              <label class="field-label" for="private-grant-expiry">有效期（小时）</label>
              <n-input-number id="private-grant-expiry" v-model:value="policyState.expiryHours" :min="0.1" :max="8760" :disabled="policyState.saving || policyState.loading" />
            </div>
            <div class="form-field toggle-field">
              <n-checkbox v-model:checked="policyState.allowPrivateHistory" :disabled="policyState.saving || policyState.loading">允许模型读取此私聊历史
              </n-checkbox>
              <p class="form-hint">默认关闭。图片、工具和群学习能力需各自授权，本入口仅开放文字私聊。</p>
            </div>
          </div>
          <div class="button-row">
            <n-button attr-type="submit" type="primary" :disabled="privateGrantDisabled">加入私聊权限草稿
            </n-button>
          </div>
        </form>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">非个人共享</p>
            <h2>选定来源、目标与具体内容</h2>
            <p class="subtle-text">每条许可只开放选定的当前投影。人物事实、私聊、原文、群资料及关系不共享；来源或版本失效后旧回答不能继续使用。</p>
          </div>
          <n-tag :type="policyState.snapshot.cross_group_sharing_enabled ? 'success' : 'default'">{{ policyState.snapshot.cross_group_sharing_enabled ? '运行版已启用' : '运行版未启用' }}</n-tag>
        </div>
        <div class="form-grid">
          <div class="form-field">
            <label class="field-label" for="sharing-source">来源群 ID</label>
            <n-input id="sharing-source" v-model:value="policyState.visibilitySourceGroupId" :disabled="policyState.saving || policyState.loading" placeholder="精确来源群" />
          </div>
          <div class="form-field">
            <label class="field-label" for="sharing-target">目标群 ID</label>
            <n-input id="sharing-target" v-model:value="policyState.visibilityTargetGroupId" :disabled="policyState.saving || policyState.loading" placeholder="另一个精确群" />
          </div>
          <div class="form-field">
            <label class="field-label" for="sharing-material">内容类型</label>
            <n-select id="sharing-material" v-model:value="policyState.visibilityMaterial" :options="visibilityMaterialOptions" :disabled="policyState.saving || policyState.loading" />
          </div>
          <div class="form-field">
            <label class="field-label" for="sharing-grant-id">许可编号</label>
            <n-input id="sharing-grant-id" v-model:value="policyState.visibilityGrantId" :maxlength="64" :disabled="policyState.saving || policyState.loading" placeholder="本条许可的唯一名称" />
          </div>
          <div class="form-field">
            <label class="field-label" for="sharing-hours">有效期（小时）</label>
            <n-input-number id="sharing-hours" v-model:value="policyState.expiryHours" :min="0.1" :max="8760" :disabled="policyState.saving || policyState.loading" />
          </div>
        </div>
        <div class="button-row">
          <n-button :disabled="policyState.saving || policyState.loading" @click="addVisibilityGovernanceDraft">加入两群管理许可草稿</n-button>
          <n-button :loading="policyState.visibilityOptionsLoading" :disabled="policyState.saving || policyState.loading" @click="loadVisibilityOptions">读取来源内容清单</n-button>
        </div>
        <p class="form-hint">新群须先保存管理许可草稿，再读取内容。来源清单最多显示64项；{{ policyState.visibilityOptionsPartial ? '当前为部分清单' : `当前清单共${policyState.visibilityOptions.length}项` }}。清单不替代非个人审核，提交时会再次检查当前来源与版本。</p>
        <label class="field-label" for="sharing-objects">选定投影</label>
        <n-select id="sharing-objects" v-model:value="policyState.visibilitySelection" multiple :options="visibilityOptions" :disabled="policyState.saving || policyState.loading || policyState.visibilityOptionsLoading" placeholder="仅选择可公开到目标群的内容" />
        <n-checkbox v-model:checked="policyState.visibilityConfirmed" :disabled="policyState.saving || policyState.loading">我已审核选定内容，确认它们是非个人知识、黑话或通用表达，允许用于所填目标群。</n-checkbox>
        <div class="button-row">
          <n-button type="primary" :disabled="visibilityDisabled" @click="grantVisibility">保存共享许可</n-button>
        </div>
        <p class="form-hint">常规权限草稿须先保存或放弃。共用同一权限版本；保存共享许可不会提前启用运行版开关。</p>
        <div v-for="grant in policyState.snapshot.visibility_grants ?? []" :key="grant.grant_id" class="grant-row">
          <div>
            <strong>{{ grant.grant_id }} · {{ grant.source_scope.group_id }} → {{ grant.target_scope.group_id }}</strong>
            <p>{{ grant.material_type }} · {{ grant.object_refs.length }}项 · {{ grant.status === 'active' ? '启用记录' : '已撤销' }} · 截止 {{ grantExpiry(grant) }}</p>
          </div>
          <n-button size="small" type="error" secondary :disabled="policyState.saving || policyState.loading || hasPolicyDraftChanges() || grant.status !== 'active'" @click="revokeVisibility(grant)">撤销此许可</n-button>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">N7 后台模型</p>
            <h2>独立授权日程与 Dream</h2>
            <p class="subtle-text">使用上方的精确群 ID 和有效期，分别添加后台模型权限草稿。它们不会读取群消息，也不会发送 QQ 消息；Worldbook 门禁和群白名单仍需开启，保存权限后立即生效。</p>
          </div>
        </div>
        <div class="button-row">
          <n-button :disabled="policyState.saving || policyState.loading || !policyState.groupId.trim()" @click="addBackgroundModelGrant('schedule')">添加日程模型授权</n-button>
          <n-button :disabled="policyState.saving || policyState.loading || !policyState.groupId.trim()" @click="addBackgroundModelGrant('dream')">添加 Dream 模型授权</n-button>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">外部搜索</p>
            <h2>搜索目标单独授权</h2>
            <p class="subtle-text">使用上方群 ID、用户 ID 和有效期，允许向当前搜索站点发送查询。读取历史或图片形成的查询也需要各来源作者允许对应外传；模型授权不会授予搜索权限。</p>
          </div>
        </div>
        <p v-for="destination in searchDestinations" :key="destination.tool_id" class="form-hint">{{ destination.tool_id }} · {{ destination.destination }}</p>
        <n-button :disabled="policyState.saving || policyState.loading || !searchDestinations.length" @click="addSearchGrant">添加搜索授权草稿</n-button>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">网页读取</p>
            <h2>网页 GET / 准确 URL 单独授权</h2>
            <p class="subtle-text">使用上方群 ID、用户 ID 和有效期；输入的完整 URL（包括路径和查询参数）就是获准目标。</p>
          </div>
        </div>
        <div class="form-field">
          <label class="field-label" for="fetch-url-input">准确 HTTPS URL</label>
          <n-input id="fetch-url" :input-props="{id: 'fetch-url-input', 'aria-label': '准确 HTTPS URL'}" v-model:value="policyState.fetchUrl" :disabled="policyState.saving || policyState.loading" placeholder="https://example.com/path?query=value" />
          <p class="form-hint">主机白名单只约束可访问的主机，授权仍绑定此处完整 URL。从历史消息形成的 URL 仍需对应来源作者允许历史共享；网页返回内容按不可信数据处理。此授权只允许该准确 URL 的 GET，不授权 POST。</p>
          <p v-if="!fetchToolAvailable" class="form-hint">当前运行版本未启用网页读取。</p>
        </div>
        <div class="button-row">
          <n-button :disabled="fetchGrantDisabled" @click="addFetchGrant">添加准确 URL GET 授权草稿</n-button>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">HTTP API</p>
            <h2>精确 URL 方法授权</h2>
            <p class="subtle-text">授权绑定输入的完整 HTTPS URL，包括路径和查询参数；不会按主机或路径推导其他目标。</p>
          </div>
        </div>
        <div class="form-field">
          <label class="field-label" for="http-method-select">请求方法</label>
          <n-select id="http-method-select" v-model:value="policyState.httpMethod" :options="httpMethodOptions" :disabled="policyState.saving || policyState.loading" />
          <p class="form-hint">{{ policyState.httpMethod === 'GET' ? 'GET 仅授权读取该准确目标。' : 'POST 是外部写动作；结果未知时不会自动重试。下面只保存目标授权，不会执行请求。' }}</p>
          <label class="field-label" for="http-url-input">准确 HTTPS URL</label>
          <n-input id="http-url" :input-props="{id: 'http-url-input', 'aria-label': 'HTTP API 准确 HTTPS URL'}" v-model:value="policyState.httpUrl" :disabled="policyState.saving || policyState.loading" placeholder="https://example.com/api/resource?key=value" />
          <p v-if="!httpToolAvailable" class="form-hint">当前运行快照未启用所选请求方法。</p>
        </div>
        <div class="button-row">
          <n-button :disabled="httpGrantDisabled" @click="addHttpGrant">{{ policyState.httpMethod === 'POST' ? '添加 POST 目标授权' : '添加 GET 目标授权' }}</n-button>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">诊断许可</p>
            <h2>聊天诊断许可</h2>
            <p class="subtle-text">使用上方精确群、用户和有效期，单独新增本群诊断许可；不会附加普通模型授权。</p>
          </div>
        </div>
        <p class="form-hint">版本、工具清单、本群状态和分段信息可不调用模型进行诊断；排查模型或工具问题仍需对应的现有模型/工具许可。</p>
        <p class="form-hint">运行版本：{{ diagnosticEnabled ? '已开启' : '未开启' }}；当前群：{{ diagnosticGroupAllowed ? '在诊断名单中' : '不在诊断名单中' }}。</p>
        <div class="button-row">
          <n-button :disabled="diagnosticGrantDisabled" @click="addDiagnosticGrant">添加聊天诊断许可</n-button>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">授权清单</p>
            <h2>当前草稿 {{ policyState.draft.length }} 条</h2>
          </div>
          <n-button type="error" secondary :disabled="policyState.saving || !policyState.draft.length" @click="requestClearGrants">清空常规授权草稿</n-button>
        </div>
        <div v-if="policyState.pendingClear" class="confirm-strip">
          <p>确定清空当前 {{ policyState.draft.length }} 条常规授权？共享许可须单独撤销。点击保存后才会写入服务。</p>
          <div class="button-row">
            <n-button size="small" @click="cancelClearGrants">取消</n-button>
            <n-button size="small" type="error" @click="confirmClearGrants">确认清空草稿</n-button>
          </div>
        </div>
        <div v-if="policyState.draft.length" class="grant-table-wrap">
          <table class="grant-table">
            <thead>
              <tr>
                <th>会话 / 用户</th>
                <th>运行模型目的地</th>
                <th>操作范围</th>
                <th>媒体能力</th>
                <th>历史消息</th>
                <th>有效期</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(grant, index) in policyState.draft" :key="`${grantScopeLabel(grant)}-${grant.subject}-${grant.provider}-${grant.model}-${index}`">
                <td><strong>{{ grantScopeLabel(grant) }}</strong><br><span class="subtle">{{ grant.subject }}</span></td>
                <td v-if="grant.model === '' && grant.provider === '' && grant.actions.includes('status.read')"><strong>聊天诊断许可</strong></td>
                <td v-else-if="grant.model === 'web.fetch' || grant.model === 'http.get' || grant.model === 'http.post'"><strong>{{ grant.model === 'web.fetch' ? '网页 GET' : grant.model === 'http.get' ? 'HTTP GET' : 'HTTP POST' }}</strong><br><span class="subtle">{{ grant.provider }}</span></td>
                <td v-else><strong>{{ grant.model }}</strong><details><summary>授权标识</summary><span class="subtle">{{ grant.provider }}</span></details></td>
                <td>{{ grantActions(grant) }}</td>
                <td>{{ grantMediaCapabilities(grant) }}</td>
                <td>{{ grant.allow_history ? '允许' : '不允许' }}</td>
                <td>{{ grantExpiry(grant) }}</td>
                <td class="actions-cell">
                  <n-button size="small" type="error" secondary :disabled="policyState.saving" @click="requestDeleteGrant(index)">删除</n-button>
                  <div v-if="policyState.pendingDelete === index" class="row-confirm">
                    <span>确认删除？</span>
                    <n-button size="tiny" @click="cancelDeleteGrant">取消</n-button>
                    <n-button size="tiny" type="error" @click="confirmDeleteGrant">确认</n-button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-state">当前没有授权。新增一条明确的群和用户授权开始试聊。</div>
        <div class="button-row policy-save-row">
          <n-button type="primary" :loading="policyState.saving" :disabled="policyState.saving || !hasPolicyDraftChanges()" @click="saveChanges">保存权限</n-button>
          <n-text depth="3">保存使用当前版本 {{ policyState.baseRevision }}；冲突时请重新读取并人工确认。</n-text>
        </div>
      </n-card>
    </template>
  </section>
</template>
