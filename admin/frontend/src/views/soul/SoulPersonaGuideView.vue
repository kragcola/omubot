<script setup lang="ts">
import {
  ArrowBackOutline,
  CheckmarkCircleOutline,
  DocumentTextOutline,
  LayersOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NIcon,
  NTag,
} from 'naive-ui'

import guideMarkdown from '../../../../../docs/ai-persona-generation-rules.md?raw'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import MetricCard from '../../components/common/MetricCard.vue'

type GuideTokenType = 'h1' | 'h2' | 'h3' | 'p' | 'ul' | 'ol' | 'code'

interface GuideToken {
  type: GuideTokenType
  content?: string
  items?: string[]
  lang?: string
}

const router = useRouter()

const guideTokens = computed(() => parseGuideMarkdown(guideMarkdown))
const sectionCount = computed(() => guideTokens.value.filter(token => token.type === 'h2').length)
const checklistCount = computed(() => {
  const checklist = guideTokens.value.find(token =>
    token.type === 'h2' && token.content === '6. 检查清单',
  )
  if (!checklist) return 0
  const start = guideTokens.value.indexOf(checklist)
  const list = guideTokens.value.slice(start).find(token => token.type === 'ul')
  return list?.items?.length || 0
})

function parseGuideMarkdown(markdown: string): GuideToken[] {
  const lines = markdown.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n')
  const tokens: GuideToken[] = []
  let index = 0

  function isBoundary(line: string) {
    const trimmed = line.trim()
    return !trimmed
      || trimmed.startsWith('#')
      || trimmed.startsWith('```')
      || trimmed.startsWith('- ')
      || /^\d+\.\s+/.test(trimmed)
  }

  while (index < lines.length) {
    const line = lines[index]
    const trimmed = line.trim()

    if (!trimmed) {
      index += 1
      continue
    }

    if (trimmed.startsWith('```')) {
      const lang = trimmed.replace(/^```/, '').trim()
      const content: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        content.push(lines[index])
        index += 1
      }
      if (index < lines.length) index += 1
      tokens.push({ type: 'code', lang, content: content.join('\n').trimEnd() })
      continue
    }

    if (trimmed.startsWith('### ')) {
      tokens.push({ type: 'h3', content: trimmed.slice(4).trim() })
      index += 1
      continue
    }

    if (trimmed.startsWith('## ')) {
      tokens.push({ type: 'h2', content: trimmed.slice(3).trim() })
      index += 1
      continue
    }

    if (trimmed.startsWith('# ')) {
      tokens.push({ type: 'h1', content: trimmed.slice(2).trim() })
      index += 1
      continue
    }

    if (trimmed.startsWith('- ')) {
      const items: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('- ')) {
        items.push(lines[index].trim().slice(2).trim())
        index += 1
      }
      tokens.push({ type: 'ul', items })
      continue
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = []
      while (index < lines.length && /^\d+\.\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^\d+\.\s+/, '').trim())
        index += 1
      }
      tokens.push({ type: 'ol', items })
      continue
    }

    const paragraph: string[] = []
    while (index < lines.length && !isBoundary(lines[index])) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    tokens.push({ type: 'p', content: paragraph.join(' ') })
  }

  return tokens
}

function backToSoul() {
  void router.push('/soul')
}
</script>

<template>
  <AppPage
    title="AI 人设生成规则"
    eyebrow="Persona Guide"
    description="用同一套规则指导 AI 自主生成 identity.md / instruction.md，生成后可回到人设编辑页继续结构化调整。"
  >
    <template #action>
      <div class="persona-guide-actions">
        <NButton secondary @click="backToSoul">
          <template #icon>
            <NIcon :component="ArrowBackOutline" />
          </template>
          返回人设编辑
        </NButton>
      </div>
    </template>

    <div class="persona-guide-metrics">
      <MetricCard
        title="目标文件"
        value="2"
        hint="identity.md / instruction.md"
        :icon="LayersOutline"
        accent="primary"
      />
      <MetricCard
        title="规则章节"
        :value="sectionCount"
        hint="覆盖生成、拆分、结构和检查清单"
        :icon="DocumentTextOutline"
        accent="info"
      />
      <MetricCard
        title="检查项"
        :value="checklistCount"
        hint="生成后可按清单逐项核对"
        :icon="CheckmarkCircleOutline"
        accent="success"
      />
    </div>

    <AppCard bordered elevated class="persona-guide-card">
      <div class="persona-guide-card__head">
        <div>
          <p class="persona-guide-card__eyebrow">
            AI Persona Rules
          </p>
          <h2>自主生成，不直接导入</h2>
          <p>
            这页内容来自项目文档 <code>docs/ai-persona-generation-rules.md</code>。
            它不是导入器，而是一份给 AI 和管理员共同使用的生成规则。
          </p>
        </div>
        <NTag round type="success">
          双文件人设
        </NTag>
      </div>

      <article class="persona-guide-doc">
        <template
          v-for="(token, index) in guideTokens"
          :key="`${token.type}-${index}`"
        >
          <h1 v-if="token.type === 'h1'">
            {{ token.content }}
          </h1>
          <h2 v-else-if="token.type === 'h2'">
            {{ token.content }}
          </h2>
          <h3 v-else-if="token.type === 'h3'">
            {{ token.content }}
          </h3>
          <p v-else-if="token.type === 'p'">
            {{ token.content }}
          </p>
          <ul v-else-if="token.type === 'ul'">
            <li v-for="item in token.items" :key="item">
              {{ item }}
            </li>
          </ul>
          <ol v-else-if="token.type === 'ol'">
            <li v-for="item in token.items" :key="item">
              {{ item }}
            </li>
          </ol>
          <pre v-else-if="token.type === 'code'"><code>{{ token.content }}</code></pre>
        </template>
      </article>
    </AppCard>
  </AppPage>
</template>

<style scoped>
.persona-guide-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.persona-guide-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.persona-guide-card {
  display: grid;
  gap: 22px;
  padding: 24px;
}

.persona-guide-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 20px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: color-mix(in srgb, var(--om-surface-solid) 78%, transparent);
}

.persona-guide-card__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.persona-guide-card__head h2 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 22px;
  font-weight: 700;
}

.persona-guide-card__head p {
  margin: 10px 0 0;
  max-width: 760px;
  color: var(--om-text-2);
  font-size: 14px;
  line-height: 1.7;
}

.persona-guide-doc {
  display: grid;
  gap: 14px;
  color: var(--om-text-1);
}

.persona-guide-doc h1 {
  margin: 0;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--om-border);
  font-size: 28px;
  font-weight: 750;
  letter-spacing: -0.02em;
}

.persona-guide-doc h2 {
  margin: 16px 0 0;
  padding: 18px 18px 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.persona-guide-doc h3 {
  margin: 8px 0 0;
  padding: 0 18px;
  color: var(--om-primary);
  font-size: 15px;
  font-weight: 700;
}

.persona-guide-doc p,
.persona-guide-doc ul,
.persona-guide-doc ol,
.persona-guide-doc pre {
  margin: 0 18px;
}

.persona-guide-doc p {
  color: var(--om-text-2);
  font-size: 14px;
  line-height: 1.85;
}

.persona-guide-doc ul,
.persona-guide-doc ol {
  display: grid;
  gap: 8px;
  padding: 16px 20px 16px 38px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
  color: var(--om-text-2);
  line-height: 1.7;
}

.persona-guide-doc pre {
  overflow: auto;
  padding: 18px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 82%, transparent);
  color: var(--om-text-1);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

@media (max-width: 900px) {
  .persona-guide-metrics {
    grid-template-columns: 1fr;
  }

  .persona-guide-card,
  .persona-guide-card__head {
    padding: 16px;
  }

  .persona-guide-card__head {
    display: grid;
  }

  .persona-guide-doc p,
  .persona-guide-doc ul,
  .persona-guide-doc ol,
  .persona-guide-doc pre,
  .persona-guide-doc h2,
  .persona-guide-doc h3 {
    margin-right: 0;
    margin-left: 0;
    padding-right: 0;
    padding-left: 0;
  }
}
</style>
