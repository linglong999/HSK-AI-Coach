// pages/Help.tsx · 帮助页：复刻 legacy howto 教程弹窗（教学动线/卡片/标注）
import { t } from '../lib/i18n'

function Html({ k, className }: { k: string; className?: string }) {
  return <span className={className} dangerouslySetInnerHTML={{ __html: t(k) }} />
}

export default function Help() {
  return (
    <div className="panel htu">
      <h3>{t('htu1T')}</h3>
      <p className="sub"><Html k="htu1SHtml" /></p>
      <div className="ctypes">
        <div className="ctype">
          <div className="ar">↗</div>
          <div className="ct">{t('htuDig')}</div>
          <div className="cd">{t('htuDigD')}</div>
        </div>
        <div className="ctype">
          <div className="ar">→</div>
          <div className="ct">{t('htuAdj')}</div>
          <div className="cd"><Html k="htuAdjD" /></div>
        </div>
        <div className="ctype">
          <div className="ar">↓</div>
          <div className="ct">{t('htuRv')}</div>
          <div className="cd">{t('htuRvD')}</div>
        </div>
      </div>

      <h3>{t('htu2T')}</h3>
      <ul>
        <li><Html k="htu2LHtml" /></li>
      </ul>

      <h3>{t('htu3T')}</h3>
      <ul>
        <li><Html k="htu3aHtml" /></li>
        <li><Html k="htu3bHtml" /></li>
        <li><Html k="htu3cHtml" /></li>
      </ul>

      <h3>{t('htu4T')}</h3>
      <ul>
        <li>{t('htu4a')}</li>
        <li>{t('htu4b')}</li>
      </ul>
    </div>
  )
}