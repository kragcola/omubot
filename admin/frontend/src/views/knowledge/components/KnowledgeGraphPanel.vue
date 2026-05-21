<script setup lang="ts">
import { DocumentTextOutline, FlashOutline, LayersOutline } from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import { percentText, relationshipEvidenceText, relationshipScopeText } from '../helpers/formatters'
import type { GraphEntity, GraphRelationship, SupersedeDraft } from '../helpers/types'

interface Props {
  graphEntities: GraphEntity[]
  graphRelationships: GraphRelationship[]
  graphScopeRisks: GraphRelationship[]
  graphLoading: boolean
  graphUnsupported: boolean
  factBusy: string
}

defineProps<Props>()

const factRollbackNotes = defineModel<Record<string, string>>('factRollbackNotes', { required: true })
const supersedeDrafts = defineModel<Record<string, SupersedeDraft>>('supersedeDrafts', { required: true })

const emit = defineEmits<{
  (e: 'rollback', rel: GraphRelationship): void
  (e: 'supersede', rel: GraphRelationship): void
}>()
</script>

<template>
  <NSpin :show="graphLoading">
    <div class="graph-layout">
      <AppPanelSection eyebrow="Entities" title="实体" class="graph-entities">
        <template #aside>
          <NTag round size="small">
            {{ graphEntities.length }} 个
          </NTag>
        </template>
        <div v-if="graphEntities.length" class="entity-list">
          <div v-for="entity in graphEntities" :key="entity.name" class="entity-row">
            <span>{{ entity.name }}</span>
            <NTag round size="small">
              {{ entity.fact_count }} 条
            </NTag>
          </div>
        </div>
        <EmptyState
          v-else
          compact
          title="暂无实体"
          description="通过候选审核或后续自动抽取后会出现实体。"
          :icon="LayersOutline"
        />
      </AppPanelSection>

      <div class="relationship-list">
        <EmptyState
          v-if="graphUnsupported"
          title="当前后端还没有图谱接口"
          description="新版前端已经加载，但运行容器仍是旧后端。请重建/重启 Bot 后再查看图谱关系。"
          :icon="FlashOutline"
        />
        <NAlert
          v-if="!graphUnsupported && graphScopeRisks.length"
          type="warning"
          :show-icon="false"
          class="graph-scope-risk"
        >
          <div class="graph-scope-risk__head">
            <strong>发现 {{ graphScopeRisks.length }} 条历史全局事实需要复核</strong>
            <span>这些事实带有记忆卡片证据，但缺少用户/群作用域，可能来自旧版本迁移。确认不该全局可见时，请回滚事实。</span>
          </div>
          <div class="graph-scope-risk__list">
            <div
              v-for="rel in graphScopeRisks.slice(0, 5)"
              :key="`risk-${rel.fact_id}`"
              class="graph-scope-risk__item"
            >
              <span>{{ rel.subject }} {{ rel.predicate }} {{ rel.object }}</span>
              <NButton
                size="tiny"
                secondary
                type="warning"
                :loading="factBusy === rel.fact_id"
                @click="emit('rollback', rel)"
              >
                回滚
              </NButton>
            </div>
          </div>
        </NAlert>
        <AppCard
          v-for="rel in graphRelationships"
          :key="rel.fact_id"
          bordered
          embedded
          class="relationship-card"
        >
          <div class="relationship-card__triple">
            <strong>{{ rel.subject }}</strong>
            <span>{{ rel.predicate }}</span>
            <strong>{{ rel.object }}</strong>
          </div>
          <p class="relationship-card__evidence">
            {{ relationshipEvidenceText(rel) }}
          </p>
          <div class="relationship-card__meta">
            <NTag round size="small" type="success">
              {{ percentText(rel.confidence) }}
            </NTag>
            <span>{{ rel.source }}</span>
            <span>{{ relationshipScopeText(rel) }}</span>
            <span>{{ rel.fact_id }}</span>
            <span v-if="rel.supersedes">取代 {{ rel.supersedes }}</span>
          </div>
          <div
            v-if="supersedeDrafts[rel.fact_id]"
            class="relationship-card__governance"
          >
            <div class="relationship-card__rollback">
              <NInput
                v-model:value="factRollbackNotes[rel.fact_id]"
                clearable
                placeholder="回滚备注，可选"
              />
              <NButton
                secondary
                type="warning"
                :loading="factBusy === rel.fact_id"
                @click="emit('rollback', rel)"
              >
                回滚事实
              </NButton>
            </div>
            <div class="relationship-card__supersede">
              <NInput
                v-model:value="supersedeDrafts[rel.fact_id].subject"
                placeholder="主体"
              />
              <NInput
                v-model:value="supersedeDrafts[rel.fact_id].predicate"
                placeholder="关系"
              />
              <NInput
                v-model:value="supersedeDrafts[rel.fact_id].object"
                placeholder="客体"
              />
              <NInput
                v-model:value="supersedeDrafts[rel.fact_id].note"
                placeholder="取代说明，可选"
              />
              <NButton
                type="primary"
                secondary
                :loading="factBusy === rel.fact_id"
                @click="emit('supersede', rel)"
              >
                取代事实
              </NButton>
            </div>
          </div>
        </AppCard>
        <EmptyState
          v-if="!graphUnsupported && graphRelationships.length === 0"
          title="暂无图谱事实"
          description="当前图谱底座已就绪，但还没有 active fact。"
          :icon="DocumentTextOutline"
        />
      </div>
    </div>
  </NSpin>
</template>

<style scoped>
.graph-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 0.72fr);
  gap: 16px;
  align-items: start;
}

.entity-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.entity-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.relationship-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.graph-scope-risk {
  border-radius: 14px;
}

.graph-scope-risk__head {
  display: grid;
  gap: 4px;
}

.graph-scope-risk__head strong {
  color: var(--om-text-1);
}

.graph-scope-risk__head span {
  color: var(--om-text-2);
  line-height: 1.6;
}

.graph-scope-risk__list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.graph-scope-risk__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid rgba(197, 138, 43, 0.22);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.45);
}

.graph-scope-risk__item span {
  min-width: 0;
  overflow: hidden;
  color: var(--om-text-1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.relationship-card {
  padding: 16px;
}

.relationship-card__triple {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  color: var(--om-text-1);
}

.relationship-card__triple span {
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(49, 108, 114, 0.1);
  color: var(--om-primary);
  font-size: 12px;
  font-weight: 700;
}

.relationship-card__evidence {
  margin: 10px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.relationship-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

.relationship-card__governance {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--om-border);
}

.relationship-card__rollback {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
}

.relationship-card__supersede {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) minmax(90px, 0.6fr) minmax(120px, 1fr) minmax(150px, 1fr) auto;
  gap: 10px;
  align-items: center;
}

@media (max-width: 1180px) {
  .graph-layout,
  .relationship-card__rollback,
  .relationship-card__supersede {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
