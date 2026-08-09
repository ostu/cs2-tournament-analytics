"use strict";
/*
 * Минимальный DOM для прогона dashboard/js/app.js под Node без браузера.
 * Не претендует на полноту jsdom — реализует только то, что реально
 * используется app.js: getElementById/createElement/appendChild,
 * innerHTML (реальный парсинг в дерево, не просто строка), textContent,
 * value/options, classList, dataset, addEventListener + queryAll с
 * поддержкой tag/#id/.class/[attr]/[attr=value] и descendant-комбинатора.
 */

function toKebab(s) {
  return s.replace(/([A-Z])/g, "-$1").toLowerCase();
}

function toCamel(s) {
  return s.replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
}

class TextNode {
  constructor(text) {
    this.nodeType = 3;
    this._text = text;
    this.parentNode = null;
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); }
}

class ElementNode {
  constructor(tagName, doc) {
    this.nodeType = 1;
    this.tagName = String(tagName || "").toUpperCase();
    this.ownerDocument = doc;
    this.attributes = {};
    this.childNodes = [];
    this.parentNode = null;
    this._listeners = {};
    this._value = "";
  }

  get id() { return this.attributes.id || ""; }
  set id(v) { this.attributes.id = v; this.ownerDocument._register(this); }

  get className() { return this.attributes["class"] || ""; }
  set className(v) { this.attributes["class"] = v; }

  get dataset() {
    const self = this;
    return new Proxy({}, {
      get(_, key) { return self.attributes["data-" + toKebab(String(key))]; },
      set(_, key, value) { self.attributes["data-" + toKebab(String(key))] = String(value); return true; },
    });
  }

  get classList() {
    const self = this;
    return {
      toggle(cls, force) {
        const set = new Set((self.attributes["class"] || "").split(/\s+/).filter(Boolean));
        const has = set.has(cls);
        const should = force === undefined ? !has : force;
        if (should) set.add(cls); else set.delete(cls);
        self.attributes["class"] = Array.from(set).join(" ");
        return should;
      },
      contains(cls) {
        return (self.attributes["class"] || "").split(/\s+/).indexOf(cls) !== -1;
      },
      add(cls) { this.toggle(cls, true); },
      remove(cls) { this.toggle(cls, false); },
    };
  }

  get textContent() {
    return this.childNodes.map(function (c) { return c.textContent; }).join("");
  }
  set textContent(v) {
    this.childNodes = [new TextNode(String(v))];
  }

  get value() { return this._value; }
  set value(v) { this._value = String(v); }

  get options() {
    return this.childNodes.filter(function (c) { return c.tagName === "OPTION"; });
  }

  get innerHTML() {
    return this.childNodes.map(serialize).join("");
  }
  set innerHTML(html) {
    this.ownerDocument._unregisterSubtree(this);
    const nodes = parseNodes(html, this.ownerDocument);
    nodes.forEach((n) => { n.parentNode = this; });
    this.childNodes = nodes;
    this.ownerDocument._registerSubtree(this);
  }

  appendChild(node) {
    node.parentNode = this;
    this.childNodes.push(node);
    if (node.nodeType === 1) this.ownerDocument._registerSubtree(node);
    return node;
  }

  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }

  dispatch(type, evt) {
    (this._listeners[type] || []).forEach(function (fn) { fn(evt || { target: this }); }, this);
  }

  querySelectorAll(sel) { return queryAll(this, sel); }
  querySelector(sel) { return queryAll(this, sel)[0] || null; }
}

function serialize(node) {
  if (node.nodeType === 3) return node._text;
  const attrs = Object.keys(node.attributes)
    .map(function (k) { return ` ${k}="${node.attributes[k]}"`; })
    .join("");
  const inner = node.childNodes.map(serialize).join("");
  const tag = node.tagName.toLowerCase();
  return `<${tag}${attrs}>${inner}</${tag}>`;
}

// ---------------------------------------------------------------------------
// HTML -> node tree (subset: no void elements needed by app.js, no comments
// inside dynamically-generated fragments)
// ---------------------------------------------------------------------------

function parseAttrs(attrStr) {
  const attrs = {};
  const re = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*("([^"]*)"|'([^']*)'))?/g;
  let m;
  while ((m = re.exec(attrStr))) {
    attrs[m[1]] = m[3] !== undefined ? m[3] : (m[4] !== undefined ? m[4] : "");
  }
  return attrs;
}

const VOID_TAGS = new Set(["br", "img", "input", "hr", "meta", "link"]);

function parseNodes(html, doc) {
  const tokenRe = /<!--[\s\S]*?-->|<\/?[a-zA-Z][^>]*>|[^<]+/g;
  const root = { childNodes: [] };
  const stack = [root];
  let m;
  while ((m = tokenRe.exec(html))) {
    const tok = m[0];
    if (tok.startsWith("<!--")) continue;
    if (tok[0] === "<") {
      const closing = tok[1] === "/";
      if (closing) {
        if (stack.length > 1) stack.pop();
        continue;
      }
      const selfClose = /\/>$/.test(tok);
      const tagMatch = tok.match(/^<([a-zA-Z][-a-zA-Z0-9]*)/);
      const tagName = tagMatch[1];
      const attrStr = tok.slice(tagMatch[0].length, tok.length - (selfClose ? 2 : 1));
      const el = new ElementNode(tagName, doc);
      el.attributes = parseAttrs(attrStr);
      const parent = stack[stack.length - 1];
      parent.childNodes.push(el);
      el.parentNode = parent === root ? null : parent;
      if (!selfClose && !VOID_TAGS.has(tagName.toLowerCase())) {
        stack.push(el);
      }
    } else {
      const text = tok.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      const parent = stack[stack.length - 1];
      const textNode = new TextNode(text);
      parent.childNodes.push(textNode);
      textNode.parentNode = parent === root ? null : parent;
    }
  }
  return root.childNodes;
}

// ---------------------------------------------------------------------------
// tiny selector engine: tag, #id, .class (multiple), [attr], [attr="value"],
// descendant combinator (space-separated steps)
// ---------------------------------------------------------------------------

function compileStep(step) {
  const tagMatch = step.match(/^[a-zA-Z][-a-zA-Z0-9]*/);
  const tag = tagMatch ? tagMatch[0].toUpperCase() : null;
  const classes = (step.match(/\.[-\w]+/g) || []).map(function (c) { return c.slice(1); });
  const idMatch = step.match(/#([-\w]+)/);
  const id = idMatch ? idMatch[1] : null;
  const attrDefs = (step.match(/\[[^\]]+\]/g) || []).map(function (a) {
    const mm = a.match(/\[([-\w]+)(?:=("([^"]*)"|([^\]]*)))?\]/);
    return { name: mm[1], value: mm[2] !== undefined ? (mm[3] !== undefined ? mm[3] : mm[4]) : undefined };
  });
  return function (el) {
    if (!el || el.nodeType !== 1) return false;
    if (tag && el.tagName !== tag) return false;
    if (id && el.id !== id) return false;
    if (classes.length) {
      const elClasses = (el.className || "").split(/\s+/);
      if (!classes.every(function (c) { return elClasses.indexOf(c) !== -1; })) return false;
    }
    for (const a of attrDefs) {
      const val = el.attributes[a.name];
      if (val === undefined) return false;
      if (a.value !== undefined && val !== a.value) return false;
    }
    return true;
  };
}

function descendants(context) {
  const out = [];
  (function walk(node) {
    (node.childNodes || []).forEach(function (c) {
      if (c.nodeType === 1) { out.push(c); walk(c); }
    });
  })(context);
  return out;
}

function hasAncestorMatching(el, boundaryParent, pred) {
  let p = el.parentNode;
  while (p && p !== boundaryParent) {
    if (pred(p)) return true;
    p = p.parentNode;
  }
  return false;
}

function queryAll(context, selector) {
  const steps = selector.trim().split(/\s+/).map(compileStep);
  const all = descendants(context);
  let results = all.filter(steps[steps.length - 1]);
  for (let i = steps.length - 2; i >= 0; i--) {
    const pred = steps[i];
    results = results.filter(function (el) { return hasAncestorMatching(el, context.parentNode, pred); });
  }
  return results;
}

// ---------------------------------------------------------------------------
// document
// ---------------------------------------------------------------------------

class FakeDocument {
  constructor() {
    this._byId = {};
    this._readyHandlers = [];
    this.documentElement = new ElementNode("html", this);
    this.documentElement.ownerDocument = this;
  }

  _register(el) {
    if (el.id) this._byId[el.id] = el;
  }
  _registerSubtree(el) {
    this._register(el);
    (el.childNodes || []).forEach((c) => { if (c.nodeType === 1) this._registerSubtree(c); });
  }
  _unregisterSubtree(el) {
    (el.childNodes || []).forEach((c) => {
      if (c.nodeType !== 1) return;
      if (c.id && this._byId[c.id] === c) delete this._byId[c.id];
      this._unregisterSubtree(c);
    });
  }

  getElementById(id) { return this._byId[id] || null; }

  createElement(tag) { return new ElementNode(tag, this); }

  querySelectorAll(sel) { return queryAll(this.documentElement, sel); }
  querySelector(sel) { return queryAll(this.documentElement, sel)[0] || null; }

  addEventListener(type, fn) {
    if (type === "DOMContentLoaded") this._readyHandlers.push(fn);
  }

  fireReady() { this._readyHandlers.forEach(function (fn) { fn(); }); }
}

function createDocumentFromHtml(bodyHtml) {
  const doc = new FakeDocument();
  const nodes = parseNodes(bodyHtml, doc);
  nodes.forEach((n) => { n.parentNode = doc.documentElement; });
  doc.documentElement.childNodes = nodes;
  doc._registerSubtree(doc.documentElement);
  return doc;
}

module.exports = { createDocumentFromHtml, ElementNode, TextNode };
