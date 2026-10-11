import { lang, t } from '../lib/i18n'

export interface Scene {
  scene_id: string
  title: string
  title_zh: string
  level: number[]
  goals: string[]
  seed: string
}

export function sceneTitle(scene: Pick<Scene, 'title' | 'title_zh'>): string {
  return lang() === 'zh' ? scene.title_zh : scene.title
}

export default function ScenePicker({ scenes, disabled, onPick }: {
  scenes: Scene[]; disabled: boolean; onPick: (scene: Scene) => void
}) {
  if (!scenes.length) return null
  return <section className="scene-picker" aria-label={t('sceneH')}>
    <h3>{t('sceneH')}</h3>
    <div className="scene-grid">{scenes.map((scene) => <button type="button" className="scene-card"
      key={scene.scene_id} disabled={disabled} onClick={() => onPick(scene)}>
      <strong>{sceneTitle(scene)}</strong>
      <span>{t('sceneLevel')} {scene.level.join('–')}</span>
      <span>{t('sceneGoals')} {scene.goals.slice(0, 3).join(' / ')}</span>
    </button>)}</div>
  </section>
}
