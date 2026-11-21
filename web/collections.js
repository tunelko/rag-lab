import {$, node} from './ui.js';
import {t} from './i18n.js';
import {collectionId} from './collection-scope.js';
import {randomId} from './ids.js';

export class Collections {
  constructor(api) {
    this.api = api;
    this.records = [];
    this.dialog = $('collection-dialog');
    this.form = $('collection-form');
    this.name = $('collection-name');
    this.message = $('collection-message');
    this.submit = $('collection-save');
    this.cancel = $('collection-cancel');
    for (const id of ['collection-select', 'graph-collection-select']) {
      $(id).addEventListener('change', event => this.select(event.target.value));
    }
    $('new-collection').addEventListener('click', () => {
      this.key = `library-${randomId()}`;
      this.form.reset();
      this.message.hidden = true;
      this.dialog.showModal();
    });
    this.cancel.addEventListener('click', () => this.dialog.close());
    this.dialog.addEventListener('cancel', event => {
      if (this.submit.disabled) event.preventDefault();
    });
    this.form.addEventListener('submit', event => {
      event.preventDefault();
      this.create();
    });
    window.addEventListener('rag:languagechange', () => this.render());
  }

  async load() {
    this.records = (await this.api('/api/collections')).collections;
    this.render();
  }

  render() {
    for (const id of ['collection-select', 'graph-collection-select']) {
      const select = $(id);
      select.replaceChildren(...this.records.map(row => {
        const option = node('option', '', row.id === 'default' ? t('collections.default') : row.name);
        option.value = row.id;
        return option;
      }));
      select.value = collectionId;
    }
  }

  select(id) {
    sessionStorage.setItem('musa:collection', id);
    const url = new URL(location.href);
    if (id === 'default') url.searchParams.delete('collection');
    else url.searchParams.set('collection', id);
    location.assign(url);
  }

  async create() {
    const name = this.name.value.trim();
    if (!name || this.submit.disabled) return;
    this.message.hidden = true;
    this.submit.disabled = this.cancel.disabled = this.name.disabled = true;
    try {
      const collection = await this.api('/api/collections', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: this.key, name}),
      });
      this.select(collection.id);
    } catch (error) {
      this.message.textContent = error.message;
      this.message.hidden = false;
    } finally {
      this.submit.disabled = this.cancel.disabled = this.name.disabled = false;
    }
  }
}
