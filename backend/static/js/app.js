// Progressive enhancements for server rendered pages. Every page works without this file.

document.addEventListener('submit', (event) => {
  const form = event.target
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
    event.preventDefault()
    return
  }

  // Disable the buttons of the busy area and show a spinner on the one that was pressed.
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

// "Load more" links: <a href="..." data-load-more="list-id">. Without JS the link opens the next page.
// With JS the next page is fetched, its items are appended to #list-id and the link is replaced by the new one.
document.addEventListener('click', async (event) => {
  const link = event.target.closest('a[data-load-more]')
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return
  const list = document.getElementById(link.dataset.loadMore)
  if (!list) return
  event.preventDefault()
  if (link.getAttribute('aria-busy') === 'true') return
  link.setAttribute('aria-busy', 'true')
  const spinner = document.createElement('span')
  spinner.className = 'spinner spinner--sm'
  spinner.setAttribute('aria-hidden', 'true')
  link.prepend(spinner)
  try {
    const response = await fetch(link.href, { credentials: 'same-origin' })
    if (!response.ok) throw new Error(String(response.status))
    const page = new DOMParser().parseFromString(await response.text(), 'text/html')
    const items = page.getElementById(link.dataset.loadMore)
    if (!items) throw new Error('missing list')
    list.append(...items.children)
    const next = page.querySelector(`a[data-load-more="${link.dataset.loadMore}"]`)
    if (next) {
      link.replaceWith(document.importNode(next, true))
    } else {
      const wrapper = link.closest('.load-more')
      ;(wrapper || link).remove()
    }
  } catch {
    window.location.href = link.href
  }
})
