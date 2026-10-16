'use strict';
// A lost HTTP response must not become a new closure after a page reload.
// Storage is tab-local and scoped to the municipal instance and signed-in user.
// No password, session cookie or CSRF token is stored here.
(() => {
  class Publication {
    constructor(storage, scope) {
      this.storage = storage;
      this.key = 'via:publication:' + JSON.stringify(scope);
      this.pending = null;
      const raw = storage.getItem(this.key);
      if (raw) {
        try {
          const value = JSON.parse(raw);
          if (value.action !== 'publish_closure' || typeof value.requestId !== 'string' ||
              !/^[a-f0-9-]{36}$/i.test(value.requestId) || !Array.isArray(value.selection)) throw new Error();
          this.pending = value;
        } catch (_) {
          throw new Error('No se pudo leer la publicación pendiente. No publiques otra: pedí revisión local de esta pestaña.');
        }
      }
    }
    stage(payload) {
      if (this.pending && JSON.stringify(this.pending) !== JSON.stringify(payload)) {
        throw new Error('Primero comprobá la publicación pendiente para no duplicar un cierre.');
      }
      const raw = JSON.stringify(payload);
      // Persist BEFORE sending. If persistence fails, do not send the request.
      this.storage.setItem(this.key, raw);
      this.pending = JSON.parse(raw);
      return this.pending;
    }
    clear() {
      this.storage.removeItem(this.key);
      this.pending = null;
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = Publication;
  else globalThis.ViaPublication = Publication;
})();
