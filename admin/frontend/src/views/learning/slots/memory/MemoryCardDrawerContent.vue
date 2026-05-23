<script setup lang="ts">
import { TimeOutline } from '@vicons/ionicons5'
import { computed } from 'vue'
import EmptyState from '../../../../components/common/EmptyState.vue'
import { useMemoryConsoleInject } from './state'

const console_ = useMemoryConsoleInject()
const {
  cardDetail,
  cardDrawerVisible,
  cardLoading,
  cardError,
  closeCardDetail,
  expireCard,
} = console_

const showDrawer = computed({
  get: () => cardDrawerVisible.value,
  set: (val: boolean) => {
    if (!val) closeCardDetail()
  },
})

function statusType(status: string): 'success' | 'warning' | 'default' {
  if (status === 'active') return 'success'
  if (status === 'expired') return 'warning'
  return 'default'
}
</script>

<template>
  <NDrawer v-model:show="showDrawer" :width="640" placement="right">
    <NDrawerContent
      :title="cardDetail?.card_id || '记忆卡片详情'"
      :native-scrollbar="false"
    >
      <div v-if="cardLoading" class="mc-card__loading">
        <NSpin size="small" />
      </div>
      <NAlert v-else-if="cardError" type="error" :bordered="false">
        {{ cardError }}
      </NAlert>
      <EmptyState
        v-else-if="!cardDetail"
        compact
        title="未选中卡片"
        description="点击列表中的“配置”以查看详情。"
        :icon="TimeOutline"
      />
      <div v-else class="mc-card">
        <section class="mc-card__section">
          <header class="mc-card__tags">
            <NTag size="small" :type="statusType(cardDetail.status)">
              {{ cardDetail.status }}
            </NTag>
            <NTag size="small">
              {{ cardDetail.category_label || cardDetail.category }}
            </NTag>
            <NTag size="small">
              {{ cardDetail.scope_label || cardDetail.scope }}{{ cardDetail.scope_id ? ` · ${cardDetail.scope_id}` : '' }}
            </NTag>
          </header>
          <p class="mc-card__content">
            {{ cardDetail.content || '——' }}
          </p>
        </section>

        <section class="mc-card__section">
          <h4 class="mc-card__heading">
            指标
          </h4>
          <div class="mc-card__grid">
            <div class="mc-card__item">
              <div class="mc-card__label">
                置信度
              </div>
              <div>{{ Math.round((cardDetail.confidence || 0) * 100) }}%</div>
            </div>
            <div class="mc-card__item">
              <div class="mc-card__label">
                优先级
              </div>
              <div>{{ cardDetail.priority }}</div>
            </div>
            <div class="mc-card__item">
              <div class="mc-card__label">
                来源
              </div>
              <div>{{ cardDetail.source || '——' }}</div>
            </div>
            <div class="mc-card__item">
              <div class="mc-card__label">
                Card ID
              </div>
              <div class="mc-card__mono">
                {{ cardDetail.card_id }}
              </div>
            </div>
            <div v-if="cardDetail.series_id" class="mc-card__item">
              <div class="mc-card__label">
                系列
              </div>
              <div class="mc-card__mono">
                {{ cardDetail.series_id }}
              </div>
            </div>
            <div class="mc-card__item">
              <div class="mc-card__label">
                创建
              </div>
              <div>{{ cardDetail.created_at }}</div>
            </div>
            <div class="mc-card__item">
              <div class="mc-card__label">
                更新
              </div>
              <div>{{ cardDetail.updated_at }}</div>
            </div>
          </div>
        </section>
      </div>

      <template v-if="cardDetail" #footer>
        <NSpace justify="space-between" align="center" :size="8">
          <NButton tag="a" href="/admin/memory?view=manage" size="small" quaternary>
            前往记忆管理
          </NButton>
          <NPopconfirm
            v-if="cardDetail.status === 'active'"
            positive-text="过期"
            negative-text="取消"
            @positive-click="expireCard"
          >
            <template #trigger>
              <NButton type="warning" size="small">
                标记过期
              </NButton>
            </template>
            过期后此卡片不再注入 prompt，可在记忆管理面板恢复。
          </NPopconfirm>
        </NSpace>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.mc-card {
  padding: 4px 2px 16px;
}

.mc-card__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 0;
}

.mc-card__section {
  margin-bottom: 22px;
}

.mc-card__section + .mc-card__section {
  margin-top: 22px;
  padding-top: 22px;
  border-top: 1px dashed var(--om-border);
}

.mc-card__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.mc-card__content {
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.mc-card__heading {
  margin: 0 0 12px;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.mc-card__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px 18px;
}

.mc-card__label {
  margin-bottom: 4px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
}

.mc-card__mono {
  font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: 12px;
  color: var(--om-text-2);
  word-break: break-all;
}
</style>
