document.addEventListener('DOMContentLoaded', () => {
  const navButtons = Array.from(document.querySelectorAll('[data-target]'));
  const views = Array.from(document.querySelectorAll('[data-view]'));
  const counters = {
    confirmed: document.querySelector('[data-count="confirmed"]'),
    pending: document.querySelector('[data-count="pending"]'),
    blocked: document.querySelector('[data-count="blocked"]'),
    outbox: document.querySelector('[data-count="outbox"]'),
  };

  function showView(target) {
    views.forEach((view) => {
      const isActive = view.dataset.view === target;
      view.hidden = !isActive;
      view.classList.toggle('active', isActive);
    });

    navButtons.forEach((button) => {
      const isActive = button.dataset.target === target;
      button.classList.toggle('active', isActive);
      button.setAttribute('aria-pressed', String(isActive));
    });

    if (history.replaceState) {
      history.replaceState(null, '', `#${target}`);
    }
  }

  navButtons.forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.target));
  });

  const requestedView = window.location.hash.replace('#', '');
  if (requestedView && views.some((view) => view.dataset.view === requestedView)) {
    showView(requestedView);
  }

  function setCount(name, value) {
    if (counters[name]) {
      counters[name].textContent = String(value);
    }
  }

  function getCount(name) {
    return Number.parseInt(counters[name]?.textContent || '0', 10);
  }

  function approveJob() {
    const statusCell = document.querySelector('[data-status-cell]');
    const row = document.querySelector('[data-job-row="po-nc"]');
    const queueStatus = document.querySelector('[data-queue-status]');
    const calendarEntry = document.querySelector('[data-calendar-nc]');

    if (!statusCell || statusCell.classList.contains('confirmed')) {
      return;
    }

    statusCell.textContent = 'Confirmed';
    statusCell.classList.remove('pending');
    statusCell.classList.add('confirmed');
    row?.classList.add('row-confirmed');
    queueStatus.textContent = '0 reviews';
    queueStatus.classList.remove('pending');
    queueStatus.classList.add('confirmed');

    if (calendarEntry) {
      calendarEntry.textContent = 'Charlotte, NC confirmed';
    }

    setCount('confirmed', getCount('confirmed') + 1);
    setCount('pending', Math.max(0, getCount('pending') - 1));
  }

  function queueReminder() {
    const list = document.querySelector('[data-outbox-list]');
    if (!list) {
      return;
    }

    const item = document.createElement('article');
    item.innerHTML = '<strong>Missing information follow-up: PO-IL-4001</strong><span>customer, sales rep, scheduling team</span>';
    list.prepend(item);
    setCount('outbox', getCount('outbox') + 1);
    showView('outbox');
  }

  document.querySelectorAll('[data-action="approve-job"]').forEach((button) => {
    button.addEventListener('click', approveJob);
  });

  document.querySelectorAll('[data-action="queue-reminder"]').forEach((button) => {
    button.addEventListener('click', queueReminder);
  });

  const segmentButtons = Array.from(document.querySelectorAll('[data-filter]'));
  const routeCards = Array.from(document.querySelectorAll('[data-route-region]'));

  segmentButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const filter = button.dataset.filter;
      segmentButtons.forEach((segment) => segment.classList.toggle('active', segment === button));
      routeCards.forEach((card) => {
        card.hidden = filter !== 'all' && card.dataset.routeRegion !== filter;
      });
    });
  });
});

