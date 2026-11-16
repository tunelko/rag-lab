import {$} from './ui.js';
import {t} from './i18n.js';

// A native modal keeps keyboard focus inside the drawer and supports Escape.
export class ContextPanel {
  constructor() {
    this.panel = $('context-panel');
    this.toggle = $('open-context');
    this.toggle.addEventListener('click', () => this.open());
    $('close-context').addEventListener('click', () => this.close());
    this.panel.addEventListener('close', () => {
      this.toggle.setAttribute('aria-expanded', 'false');
      document.body.classList.remove('context-open');
      for(const source of $('sources').children){source.classList.remove('is-selected');source.removeAttribute('aria-current');}
      const target = this.returnFocus?.isConnected && this.returnFocus.getClientRects().length ? this.returnFocus : this.toggle;
      target.focus({preventScroll: true});
    });
    this.panel.addEventListener('click', event => {
      if (event.target !== this.panel) return;
      const rect = this.panel.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) this.close();
    });
  }
  open(citation, trigger) {
    if (!this.panel.open) {
      this.returnFocus = trigger || document.activeElement;
      document.body.classList.add('context-open');
      this.panel.showModal();
      this.toggle.setAttribute('aria-expanded', 'true');
      $('close-context').focus({preventScroll: true});
    }
    if (citation) {
      const source = [...$('sources').children].find(item => item.id.endsWith(`-${citation}`));
      if (source) {
        for(const item of $('sources').children){item.classList.toggle('is-selected',item===source);item.removeAttribute('aria-current');}
        source.setAttribute('aria-current','true');
        source.classList.add('is-expanded');
        const expand=source.querySelector('.source-expand');
        if(expand){expand.textContent=t('sentinel.collapse_source');expand.setAttribute('aria-expanded','true');}
        source.tabIndex = -1;
        source.focus({preventScroll: true});
        source.scrollIntoView({block: 'start'});
      }
    }
  }
  close() { if (this.panel.open) this.panel.close(); }
}
