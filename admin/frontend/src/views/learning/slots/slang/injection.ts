import type { InjectionKey } from 'vue'
import type { SlangConsole } from '../../../slang/composables/useSlangConsole'

export const SLANG_CONSOLE_KEY: InjectionKey<SlangConsole> = Symbol('slang-console')

export function useSlangConsoleInject(): SlangConsole {
  const value = inject(SLANG_CONSOLE_KEY)
  if (!value) {
    throw new Error('SlangFoldInProvider must wrap any slang fold-in slot consumer')
  }
  return value
}
