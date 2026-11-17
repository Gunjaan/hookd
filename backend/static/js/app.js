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

// One alert in #page-messages (layouts/app.html), or in the open modal. kind: success, info, warning or danger.
const showMessage = (text, kind = 'success') => {
  const box = document.querySelector('dialog[open] .modal__messages') || document.getElementById('page-messages')
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

// Fields of the form named by data-include="<form id>" that this form's own data does not already have.
const includeFields = (form, body) => {
  const other = form.dataset.include && document.getElementById(form.dataset.include)
  if (!other) return
  for (const [name, value] of new FormData(other)) {
    if (!body.has(name)) body.append(name, value)
  }
}

// <form data-unsaved="<bar id>">: the bar shows after any edit and hides on save or reset.
const setUnsaved = (form, unsaved) => {
  const bar = form.dataset.unsaved && document.getElementById(form.dataset.unsaved)
  if (bar) bar.hidden = !unsaved
}

// <form data-api action="<api url>">: posted with fetch instead of loading a new page, the answer is applied in place.
//   data-include="<form id>" also sends that form's fields (the agent page's test sends the unsaved settings).
const submitApiForm = async (form, submitter) => {
  const scope = form.closest('[data-busy-scope]') || form
  const buttons = Array.from(scope.querySelectorAll('button:not(:disabled)'))
  buttons.forEach((button) => {
    button.disabled = true
  })
  if (submitter) setBusy(submitter, true)
  try {
    const url = submitter?.getAttribute('formaction') || form.action
    const body = new FormData(form, submitter)
    includeFields(form, body)
    applyAnswer(form, await api(url, { method: 'POST', body }))
    setUnsaved(form, false)
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

// <input data-autosubmit>: changing it submits its form (the agent page's on or off switch).
document.addEventListener('change', (event) => {
  const input = event.target.closest('[data-autosubmit]')
  if (input) input.form.requestSubmit()
})

document.addEventListener('input', (event) => {
  const form = event.target.form
  if (form?.dataset.unsaved) setUnsaved(form, true)
})

document.addEventListener('reset', (event) => setUnsaved(event.target, false))

// <button data-append-to="<field id>" data-text="...">: adds the text to the end of the field (voice style chips).
document.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-append-to]')
  const field = button && document.getElementById(button.dataset.appendTo)
  if (!field) return
  const current = field.value.trim()
  if (current.includes(button.dataset.text)) return
  field.value = current ? `${current} ${button.dataset.text}` : button.dataset.text
  field.dispatchEvent(new Event('input', { bubbles: true }))
  field.focus()
})

// <textarea data-send-shortcut>: Ctrl or Cmd + Enter submits its form with the default button (reply inbox).
document.addEventListener('keydown', (event) => {
  const field = event.target.closest('textarea[data-send-shortcut]')
  if (!field || event.key !== 'Enter' || !(event.ctrlKey || event.metaKey)) return
  event.preventDefault()
  field.form.requestSubmit()
})

// Modal: <a href="<page>" data-modal="<api url>"> opens the shared #modal (layouts/app.html) with the endpoint's
// `html` instead of leaving the page. Ctrl, Cmd or Shift click still opens the page itself.
const modal = document.getElementById('modal')
if (modal) {
  const modalBody = modal.querySelector('.modal__body')
  const modalMessages = modal.querySelector('.modal__messages')

  document.addEventListener('click', async (event) => {
    const link = event.target.closest('a[data-modal]')
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return
    event.preventDefault()
    modal.classList.remove('modal--photo')
    modalMessages.hidden = true
    modalBody.innerHTML = '<div class="modal__loading"><span class="spinner spinner--lg" aria-hidden="true"></span></div>'
    modal.showModal()
    try {
      modalBody.innerHTML = (await api(link.dataset.modal)).html
    } catch (error) {
      modal.close()
      showMessage(error.message, 'danger')
    }
  })

  // <a href="<first photo>" data-modal-images="<url> <url>">: the photos in the modal, as a carousel when there are several.
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-modal-images]')
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return
    event.preventDefault()
    const urls = link.dataset.modalImages.split(' ').filter(Boolean)
    modalMessages.hidden = true
    modal.classList.add('modal--photo')
    modalBody.replaceChildren(buildCarousel(urls, link.dataset.modalLabel || 'Photo'))
    modal.showModal()
    updateCarousel(modalBody.querySelector('[data-carousel]'))
  })

  // The close button, or a click on the backdrop (the dialog itself, outside its content). Esc closes natively.
  modal.addEventListener('click', (event) => {
    if (event.target === modal || event.target.closest('[data-modal-close]')) modal.close()
  })
  modal.addEventListener('close', () => {
    modal.querySelectorAll('video').forEach((video) => video.pause())
  })
}

// Carousel: <div data-carousel> with a .carousel__track, optional [data-carousel-step="-1|1"] buttons and
// [data-carousel-dot="<index>"] dots. Buttons and dots follow swipes too.
const carouselBehavior = () => (window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth')

const updateCarousel = (carousel) => {
  const track = carousel?.querySelector('.carousel__track')
  if (!track) return
  const index = Math.round(track.scrollLeft / Math.max(track.clientWidth, 1))
  const last = track.children.length - 1
  carousel.querySelector('[data-carousel-step="-1"]')?.toggleAttribute('disabled', index <= 0)
  carousel.querySelector('[data-carousel-step="1"]')?.toggleAttribute('disabled', index >= last)
  carousel.querySelectorAll('[data-carousel-dot]').forEach((dot, position) => {
    dot.setAttribute('aria-current', String(position === index))
  })
}

document.addEventListener('click', (event) => {
  const control = event.target.closest('[data-carousel-step], [data-carousel-dot]')
  const carousel = control?.closest('[data-carousel]')
  const track = carousel?.querySelector('.carousel__track')
  if (!track) return
  const width = track.clientWidth
  if ('carouselStep' in control.dataset) {
    track.scrollBy({ left: Number(control.dataset.carouselStep) * width, behavior: carouselBehavior() })
  } else {
    track.scrollTo({ left: Number(control.dataset.carouselDot) * width, behavior: carouselBehavior() })
  }
})

document.addEventListener('scroll', (event) => updateCarousel(event.target.closest?.('[data-carousel]')), true)
document.querySelectorAll('[data-carousel]').forEach(updateCarousel)

// Markup for a carousel of photos (the photo viewer): track, buttons and dots when there is more than one.
const buildCarousel = (urls, label) => {
  const make = (tag, className, attributes = {}) => {
    const element = document.createElement(tag)
    if (className) element.className = className
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value)
    return element
  }
  const carousel = make('div', 'carousel photo-viewer', { 'data-carousel': '' })
  const track = make('ol', 'carousel__track', { 'aria-label': label })
  urls.forEach((url, index) => {
    const item = make('li')
    item.append(make('img', '', { src: url, alt: `${label} ${index + 1} of ${urls.length}` }))
    track.append(item)
  })
  carousel.append(track)
  if (urls.length > 1) {
    const nav = make('div', 'carousel__nav')
    const arrow = document.querySelector('#carousel-arrow')
    for (const [step, name] of [['-1', 'Previous photo'], ['1', 'Next photo']]) {
      const button = make('button', `carousel__btn carousel__btn--${step === '1' ? 'next' : 'prev'}`, {
        type: 'button', 'data-carousel-step': step, 'aria-label': name,
      })
      if (arrow) button.append(arrow.content.cloneNode(true))
      nav.append(button)
    }
    const dots = make('div', 'carousel__dots')
    urls.forEach((_, index) => {
      dots.append(make('button', 'carousel__dot', { type: 'button', 'data-carousel-dot': index, 'aria-label': `Photo ${index + 1}` }))
    })
    carousel.append(nav, dots)
  }
  return carousel
}

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
    const story = draft.querySelector('[data-draft-story]')
    if (story && state.story) story.innerHTML = state.story
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
