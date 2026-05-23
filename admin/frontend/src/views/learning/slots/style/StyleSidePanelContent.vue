<script setup lang="ts">
import {
  ChatbubbleEllipsesOutline,
  CheckmarkCircleOutline,
  FlashOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import EmptyState from '../../../../components/common/EmptyState.vue'
import MetricCard from '../../../../components/common/MetricCard.vue'
import { useStyleConsoleInject } from './state'

const console_ = useStyleConsoleInject()
const { summary, lastExtractResult, profiles, feedback, enableProfile, disableProfile, rollbackProfile } = console_
</script>

<template>
  <div class="style-fold-side">
    <div class="style-fold-side__metrics">
      <MetricCard title="样本" :value="summary.total" :icon="SparklesOutline" hint="所有作用域" />
      <MetricCard title="待审" :value="summary.pending" :icon="FlashOutline" accent="warning" hint="人工判断" />
      <MetricCard title="入库" :value="summary.approved" :icon="CheckmarkCircleOutline" accent="success" hint="可注入" />
      <MetricCard title="档案" :value="summary.enabled_profile_count" :icon="ChatbubbleEllipsesOutline" accent="info" hint="启用版本" />
    </div>

    <section class="style-fold-side__panel">
      <header>
        <span>Latest Extract</span>
        <h3>最近抽取</h3>
      </header>
      <EmptyState
        v-if="!lastExtractResult"
        compact
        title="暂无抽取结果"
        description="点击上方抽取后，这里会展示扫描与候选结果。"
        :icon="FlashOutline"
      />
      <div v-else class="extract-result">
        <div class="extract-result__totals">
          <NTag size="small">
            {{ lastExtractResult.scope === 'global' ? '全局' : '本群' }}
          </NTag>
          <span>群 {{ lastExtractResult.groups.length }}</span>
          <span>有效文本 {{ lastExtractResult.text_scanned ?? lastExtractResult.scanned }}</span>
          <span v-if="lastExtractResult.backlog_text">待扫 {{ lastExtractResult.backlog_text }}</span>
          <span>候选 {{ lastExtractResult.extracted }}</span>
          <span>保存 {{ lastExtractResult.saved }}</span>
        </div>
      </div>
    </section>

    <section class="style-fold-side__panel">
      <header>
        <span>Style Profiles</span>
        <h3>动态风格档案</h3>
      </header>
      <EmptyState
        v-if="!profiles.length"
        compact
        title="暂无档案"
        description="通过已审核表达生成。"
        :icon="ChatbubbleEllipsesOutline"
      />
      <div v-else class="profile-list">
        <article
          v-for="profile in profiles"
          :key="profile.profile_id"
          class="profile-item"
        >
          <div class="profile-item__head">
            <NTag :type="profile.status === 'enabled' ? 'success' : 'default'" size="small">
              v{{ profile.version }} · {{ profile.status }}
            </NTag>
            <NSpace :size="4">
              <NButton
                v-if="profile.status !== 'enabled'"
                size="tiny"
                quaternary
                @click="enableProfile(profile)"
              >
                启用
              </NButton>
              <NButton
                v-if="profile.status === 'enabled'"
                size="tiny"
                quaternary
                @click="rollbackProfile(profile)"
              >
                回滚
              </NButton>
              <NButton
                v-if="profile.status === 'enabled'"
                size="tiny"
                quaternary
                @click="disableProfile(profile)"
              >
                禁用
              </NButton>
            </NSpace>
          </div>
          <p>{{ profile.content }}</p>
          <small>创建 {{ profile.created_at }}</small>
        </article>
      </div>
    </section>

    <section class="style-fold-side__panel">
      <header>
        <span>Feedback</span>
        <h3>反馈记录</h3>
      </header>
      <EmptyState
        v-if="!feedback.length"
        compact
        title="暂无反馈"
        description="反馈会用于后续反思与档案治理。"
      />
      <div v-else class="feedback-list">
        <article
          v-for="item in feedback"
          :key="item.feedback_id"
          class="feedback-item"
        >
          <div class="feedback-item__head">
            <NTag size="small">
              {{ item.rating }}
            </NTag>
            <span>{{ item.source }}</span>
            <span>{{ item.created_at }}</span>
          </div>
          <p>{{ item.raw_text || item.context || item.target_type }}</p>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.style-fold-side {
  display: grid;
  gap: 14px;
}

.style-fold-side__metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.style-fold-side__panel {
  display: grid;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface);
}

.style-fold-side__panel header span {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.style-fold-side__panel header h3 {
  margin: 4px 0 0;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.extract-result__totals,
.feedback-item__head,
.profile-item__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.profile-item__head {
  justify-content: space-between;
}

.profile-list,
.feedback-list {
  display: grid;
  gap: 10px;
}

.profile-item,
.feedback-item {
  padding: 10px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-2);
}

.profile-item p,
.feedback-item p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.6;
}

.profile-item small {
  color: var(--om-text-3);
  font-size: 11px;
}

.feedback-item__head span {
  color: var(--om-text-3);
  font-size: 11px;
}
</style>
