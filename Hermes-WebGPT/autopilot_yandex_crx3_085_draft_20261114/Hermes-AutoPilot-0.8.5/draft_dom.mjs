/**
 * Hermes AutoPilot draft compositor: safe owner-triggered single-tab write.
 *
 * MV3 chrome.scripting.executeScript serializes this function, therefore it is
 * self-contained and cannot rely on module-scope helper functions.
 * Never clicks Send, dispatches Enter, calls fetch, or reads conversation text.
 */
export function insertChatDraft(prompt) {
  const fail = reason => ({ok:false,reason,submitted:false});
  if (location.origin !== 'https://chatgpt.com') return fail('WRONG_ORIGIN');
  if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 16000)
    return fail('INVALID_DRAFT');
  if (!document || document.readyState === 'loading') return fail('DOCUMENT_NOT_READY');

  // Prefer the actual editor ID. An ambiguous generic selector never wins.
  const primaryEditors = [...document.querySelectorAll('#prompt-textarea')];
  // Duplicated IDs can occur during React hydration or with floating UI.
  // Never guess which copy receives a user's task.
  if (primaryEditors.length > 1) return fail('MULTIPLE_EDITORS');
  let editor = primaryEditors[0] ?? null;
  if (!editor) {
    const selectors = [
      '[data-testid="composer-input"][contenteditable="true"]',
      '[data-testid="composer-input"] textarea',
      'div.ProseMirror[contenteditable="true"]'
    ];
    const matches = [...new Set(selectors.flatMap(s => [...document.querySelectorAll(s)]))];
    if (matches.length !== 1) return fail(matches.length ? 'MULTIPLE_EDITORS' : 'NO_EDITOR');
    editor = matches[0];
  }
  if (!editor || !editor.isConnected) return fail('EDITOR_NOT_CONNECTED');
  if (editor.closest('[inert], [aria-hidden="true"]')) return fail('EDITOR_HIDDEN');
  const rect = editor.getBoundingClientRect();
  if (!rect.width || !rect.height) return fail('EDITOR_NOT_VISIBLE');
  if (editor.hasAttribute('disabled') || editor.getAttribute('aria-disabled') === 'true')
    return fail('EDITOR_DISABLED');

  const editable = editor.isContentEditable === true;
  const isTextarea = editor.tagName === 'TEXTAREA';
  if (!editable && !isTextarea) return fail('NOT_EDITABLE');
  if ((editable ? editor.innerText : editor.value)?.trim()) return fail('EXISTING_USER_TEXT');
  const attachmentSelector =
    '[data-attachment-id], [data-testid="attachment-chip"], [data-testid="file-chip"]';
  const composerRoot = editor.closest(
    'form, [data-testid="composer"], [data-testid="composer-root"], [data-testid="composer-container"]');
  // Files can be attached beside the editor, not only within it.
  if (editor.querySelector?.(attachmentSelector) ||
      composerRoot?.querySelector?.(attachmentSelector))
    return fail('EXISTING_ATTACHMENT');

  // This branch has no keypress or button click. Use the browser's editing
  // transaction to notify ProseMirror/React rather than unsafe innerHTML edits.
  try {
    editor.focus();
    if (editable) {
      const inserted = document.execCommand('insertText', false, prompt);
      if (!inserted) return fail('INSERT_COMMAND_REJECTED');
    } else {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype, 'value')?.set;
      if (typeof setter !== 'function') return fail('TEXTAREA_SETTER_MISSING');
      setter.call(editor, prompt);
      editor.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText',data:prompt}));
    }
    const actual = editable ? editor.innerText : editor.value;
    // Some rich editors normalize LF into paragraphs. Reject anything besides
    // newline/spacing normalization; do not claim success on partial insertion.
    // ProseMirror/contenteditable may convert LF to paragraph boundaries.
    // Compare the entire token sequence, not literal layout whitespace.
    const normalize = s => String(s ?? '').replace(/\u00a0/g,' ').replace(/\s+/g,' ').trim();
    if (normalize(actual) !== normalize(prompt)) return fail('DRAFT_CONTENT_UNCONFIRMED');
    return {ok:true,status:'DRAFT_FILLED_NOT_SENT',submitted:false,
      textLength:prompt.length};
  } catch {
    return fail('COMPOSER_WRITE_FAILED');
  }
}