// Page behaviour for the server rendered pages: busy buttons, confirm dialogs, and fetch calls to the JSON endpoints
// under /api/ (data-api forms, load more, draft progress and draft actions). JavaScript is assumed (see AGENTS.md).

const csrfToken = () => document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] ?? ''

// fetch a JSON endpoint. POST bodies are FormData. Throws an Error with the server's `error` text.
const api = async (url, { method = 'GET', body } = {}) => {
  const headers = { Accept: 'application/json', 'X-Requested-With': 'fetch' }
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken()
  let response
  try {
    response = await fetch(url, { method, body, headers, credentials: 'same-origin' })
  } catch {
    throw new Error('Could not reach Hookd. Check your connection and try again.')
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.')
  return data
}

// One alert in #page-messages (layouts/app.html). kind: success, info, warning or danger.
const showMessage = (text, kind = 'success') => {
  const box = document.getElementById('page-messages')
  if (!box || !text) return
  const alert = document.createElement('div')
  alert.className = `alert alert--${kind}`
  alert.setAttribute('role', kind === 'danger' ? 'alert' : 'status')
  const body = document.createElement('div')
  body.className = 'alert__body'
  body.textContent = text
  alert.append(body)
  box.replaceChildren(alert)
  box.hidden = false
}

// Spinner and disabled state on a button while its request runs.
const setBusy = (button, busy) => {
  button.disabled = busy
  button.toggleAttribute('aria-busy', busy)
  if (busy) {
    const spinner = document.createElement('span')
    spinner.className = 'spinner spinner--sm'
    spinner.setAttribute('aria-hidden', 'true')
    button.prepend(spinner)
  } else {
    button.querySelector('.spinner')?.remove()
  }
}

// Apply an answer from a data-api form: {message, message_kind, html, fragments}.
//   html: with data-replace="<selector>" it replaces the closest matching ancestor (empty html removes it); with
//         data-target="<id>" it becomes the content of #id.
//   fragments: {element id: html}, each element is replaced as a whole.
const applyAnswer = (form, data) => {
  if ('html' in data) {
    if (form.dataset.replace) {
      const element = form.closest(form.dataset.replace)
      if (data.html) element.outerHTML = data.html
      else element.remove()
    } else if (form.dataset.target) {
      const target = document.getElementById(form.dataset.target)
      target.innerHTML = data.html
      target.hidden = !data.html
    }
  }
  for (const [id, html] of Object.entries(data.fragments || {})) {
    const element = document.getElementById(id)
    if (element) element.outerHTML = html
  }
  if (data.message) showMessage(data.message, data.message_kind || 'success')
}

// <form data-api action="<api url>">: posted with fetch instead of loading a new page, the answer is applied in place.
const submitApiForm = async (form, submitter) => {
  const scope = form.closest('[data-busy-scope]') || form
  const buttons = Array.from(scope.querySelectorAll('button:not(:disabled)'))
  buttons.forEach((button) => {
    button.disabled = true
  })
  if (submitter) setBusy(submitter, true)
  try {
    const url = submitter?.getAttribute('formaction') || form.action
    applyAnswer(form, await api(url, { method: 'POST', body: new FormData(form, submitter) }))
  } catch (error) {
    showMessage(error.message, 'danger')
  }
  if (submitter) setBusy(submitter, false)
  buttons.forEach((button) => {
    button.disabled = false
  })
}

document.addEventListener('submit', (event) => {
  const form = event.target
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
    event.preventDefault()
    return
  }
  if ('api' in form.dataset) {
    event.preventDefault()
    submitApiForm(form, event.submitter)
    return
  }

  // A real form post: disable the buttons of the busy area and show a spinner on the one that was pressed.
  const scope = form.closest('[data-busy-scope]') || form
  scope.querySelectorAll('button').forEach((button) => {
    button.disabled = true
  })
  const submitter = event.submitter
  if (submitter) {
    submitter.setAttribute('aria-busy', 'true')
    const spinner = document.createElement('span')
    spinner.className = 'spinner spinner--sm'
    spinner.setAttribute('aria-hidden', 'true')
    submitter.prepend(spinner)
  }
})

// Pages restored from the back/forward cache must not keep buttons disabled.
window.addEventListener('pageshow', (event) => {
  if (!event.persisted) return
  document.querySelectorAll('[aria-busy="true"]').forEach((button) => {
    button.removeAttribute('aria-busy')
    button.querySelector('.spinner')?.remove()
  })
  document.querySelectorAll('button:disabled:not([data-disabled])').forEach((button) => {
    button.disabled = false
  })
})

// Photo thumbnails: <input type="file" data-photo-preview="list-id"> shows the chosen images in #list-id.
document.addEventListener('change', (event) => {
  const input = event.target.closest('input[data-photo-preview]')
  const list = input && document.getElementById(input.dataset.photoPreview)
  if (!list) return
  list.replaceChildren(
    ...Array.from(input.files).map((file) => {
      const item = document.createElement('li')
      const image = document.createElement('img')
      image.src = URL.createObjectURL(file)
      image.alt = file.name
      item.append(image)
      return item
    }),
  )
  list.hidden = input.files.length === 0
})

// Character counter: <textarea data-char-count="message-id"> writes "n / max characters" into #message-id.
const updateCharCount = (field) => {
  const message = document.getElementById(field.dataset.charCount)
  if (message) message.textContent = `${field.value.length} / ${field.maxLength} characters`
}
document.addEventListener('input', (event) => {
  const field = event.target.closest('textarea[data-char-count]')
  if (field) updateCharCount(field)
})
document.querySelectorAll('textarea[data-char-count]').forEach(updateCharCount)

// Load more: <button data-load-more="<api url>" data-target="<list id>">. The endpoint answers {html, next}: the
// items are appended to the list, the button keeps going with `next` or goes away. Optional data-more-label renames
// the button after the first page, data-empty-text replaces it when the first page is empty.
document.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-load-more]')
  const list = button && document.getElementById(button.dataset.target)
  if (!list || button.disabled) return
  setBusy(button, true)
  try {
    const data = await api(button.dataset.loadMore)
    list.insertAdjacentHTML('beforeend', data.html)
    if (!data.next) {
      if (button.dataset.emptyText && !list.children.length) {
        const empty = document.createElement('p')
        empty.className = 'hint'
        empty.textContent = button.dataset.emptyText
        button.replaceWith(empty)
      } else {
        ;(button.closest('.load-more') || button).remove()
      }
      return
    }
    button.dataset.loadMore = data.next
    if (button.dataset.moreLabel) button.textContent = button.dataset.moreLabel
  } catch (error) {
    showMessage(error.message, 'danger')
  }
  setBusy(button, false)
})

// Draft page (creatives/detail.html): <div data-draft="<progress url>" data-draft-status="...">.
const draft = document.querySelector('[data-draft]')
if (draft) {
  const POLL_MS = 4000
  const keys = new Map()
  draft.querySelectorAll('[data-slide-key]').forEach((slide) => keys.set(Number(slide.dataset.slideId), slide.dataset.slideKey))
  let polling = false

  // Apply a draft state from the API: title, alert, photos and the "Post now" button.
  const applyState = (state) => {
    if (draft.dataset.draftStatus === 'planning' && state.status !== 'planning') {
      window.location.reload() // The whole draft layout appears once planning is done.
      return
    }
    draft.dataset.draftStatus = state.status
    const title = draft.querySelector('[data-draft-title]')
    if (title) title.textContent = state.title
    const alertBox = draft.querySelector('[data-draft-alert]')
    if (alertBox) {
      alertBox.replaceChildren()
      if (state.alert) {
        const alert = document.createElement('div')
        alert.className = `alert alert--${state.alert.kind}`
        const body = document.createElement('div')
        body.className = 'alert__body'
        body.textContent = state.alert.text
        alert.append(body)
        alertBox.append(alert)
      }
    }
    const slides = draft.querySelector('[data-draft-slides]')
    for (const slide of state.slides || []) {
      if (keys.get(slide.id) === slide.key) continue
      keys.set(slide.id, slide.key)
      const current = slides?.querySelector(`[data-slide-id="${slide.id}"]`)
      if (current) current.outerHTML = slide.html
    }
    const publish = draft.querySelector('[data-draft-publish]')
    if (publish) publish.disabled = !state.can_post
    const hint = draft.querySelector('[data-draft-post-hint]')
    if (hint) hint.hidden = state.can_post
    if (state.working) startPolling()
  }

  const poll = async () => {
    try {
      const state = await api(draft.dataset.draft)
      applyState(state)
      if (state.working) {
        window.setTimeout(poll, POLL_MS)
        return
      }
    } catch {
      window.setTimeout(poll, POLL_MS) // A failed poll is retried.
      return
    }
    polling = false
  }

  const startPolling = () => {
    if (polling) return
    polling = true
    window.setTimeout(poll, POLL_MS)
  }

  // Buttons with data-draft-action="<api url>" POST the draft form (caption, hashtags, answers, rewrite instruction);
  // a slide's redo also sends its change text as `tweak`.
  draft.addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-draft-action]')
    if (!button || button.disabled) return
    const form = document.getElementById('draft-form')
    const body = form ? new FormData(form) : new FormData()
    const tweak = button.closest('.slide')?.querySelector('[data-tweak]')
    if (tweak) body.set('tweak', tweak.value)
    setBusy(button, true)
    try {
      const data = await api(button.dataset.draftAction, { method: 'POST', body })
      if ('caption' in data) {
        form.elements.caption.value = data.caption
        form.elements.hashtags.value = data.hashtags
        form.elements.caption.dispatchEvent(new Event('input', { bubbles: true }))
        const preview = draft.querySelector('[data-draft-caption]')
        if (preview) preview.innerText = data.full_caption
      }
      if (data.slides) applyState(data)
      if (data.message) showMessage(data.message)
    } catch (error) {
      showMessage(error.message, 'danger')
    }
    if (button.isConnected) setBusy(button, false)
  })

  // "Post now" is the one real form post: it saves the copy, publishes and opens the publishing list.
  draft.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-draft-publish]')
    if (!button || button.disabled) return
    setBusy(button, true)
    document.getElementById('draft-form')?.submit()
  })

  if (['planning', 'generating'].includes(draft.dataset.draftStatus)) startPolling()
}
