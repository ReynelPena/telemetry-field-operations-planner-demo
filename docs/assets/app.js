document.addEventListener('DOMContentLoaded', () => {
  const navButtons = Array.from(document.querySelectorAll('[data-target]'));
  const shortcutButtons = Array.from(document.querySelectorAll('[data-target-shortcut]'));
  const views = Array.from(document.querySelectorAll('[data-view]'));
  const title = document.querySelector('[data-page-title]');
  const subtitle = document.querySelector('[data-page-subtitle]');

  const detailData = {
    tx: {
      title: 'PO-TX-1001',
      customer: 'Lone Star Distribution',
      status: 'confirmed',
      recommendation: 'Carlos Martinez, May 12 08:00-15:30',
      materials: 'Longer display mounting screws, zip ties, cable protectors, spare connectors',
      notes: 'Toyota LP is low difficulty. This Dallas job is confirmed and grouped with nearby DFW work.',
    },
    ca: {
      title: 'PO-CA-2002',
      customer: 'Pacific Cold Storage',
      status: 'confirmed',
      recommendation: 'Priya Shah, May 12 08:00-18:00',
      materials: 'Relay prep kit, insulated tools, diagnostic laptop, spare fuses',
      notes: 'High-difficulty Jungheinrich electric work. Requires a lead-level technician.',
    },
    nc: {
      title: 'PO-NC-3001',
      customer: 'Blue Ridge Equipment',
      status: 'pending_approval',
      recommendation: 'Sofia Lopez, May 19 08:00-12:40',
      materials: 'Zip ties, cable protectors, insulated connectors, industrial tape',
      notes: 'Required information is complete. Coordinator must approve before confirmation.',
    },
    il: {
      title: 'PO-IL-4001',
      customer: 'Midwest Food Logistics',
      status: 'waiting_required_info',
      recommendation: 'Mike Johnson, May 22 08:00-13:10',
      materials: 'Toyota LP standard kit, longer display mounting screws',
      notes: 'Blocked until address, contact phone, voltage, asset IDs, and shipment tracking are provided.',
    },
  };

  function countElements(name) {
    return Array.from(document.querySelectorAll(`[data-count="${name}"], [data-count="${name}-inline"]`));
  }

  function setCount(name, value) {
    countElements(name).forEach((element) => {
      element.textContent = String(value);
    });
  }

  function getCount(name) {
    const first = countElements(name)[0];
    return Number.parseInt(first?.textContent || '0', 10);
  }

  function showView(target, focusView = false) {
    const nextView = views.find((view) => view.dataset.view === target);
    if (!nextView) {
      return;
    }

    views.forEach((view) => {
      const isActive = view === nextView;
      view.hidden = !isActive;
      view.classList.toggle('active', isActive);
    });

    navButtons.forEach((button) => {
      const isActive = button.dataset.target === target;
      button.classList.toggle('active', isActive);
      button.setAttribute('aria-pressed', String(isActive));
    });

    if (title) {
      title.textContent = nextView.dataset.title || 'Telemetry Field Operations Planner';
    }

    if (subtitle) {
      subtitle.textContent = nextView.dataset.subtitle || '';
    }

    if (history.replaceState) {
      history.replaceState(null, '', `#${target}`);
    }

    if (focusView) {
      nextView.focus({ preventScroll: true });
    }
  }

  function approveJob() {
    const statusCell = document.querySelector('[data-status-cell]');
    const jobStatus = document.querySelector('[data-job-nc-status]');
    const row = document.querySelector('[data-job-row="po-nc"]');
    const queueStatus = document.querySelector('[data-queue-status]');
    const calendarEntry = document.querySelector('[data-calendar-nc]');

    if (!statusCell || statusCell.classList.contains('confirmed')) {
      showView('calendar');
      return;
    }

    [statusCell, jobStatus].filter(Boolean).forEach((element) => {
      element.textContent = 'Confirmed';
      element.classList.remove('pending');
      element.classList.add('confirmed');
    });

    row?.classList.add('row-confirmed');

    if (queueStatus) {
      queueStatus.textContent = '0 reviews';
      queueStatus.classList.remove('pending');
      queueStatus.classList.add('confirmed');
    }

    if (calendarEntry) {
      calendarEntry.textContent = '08:00 Charlotte, NC - Sofia';
    }

    setCount('confirmed', getCount('confirmed') + 1);
    setCount('pending', Math.max(0, getCount('pending') - 1));
    showView('calendar');
  }

  function queueReminder() {
    const list = document.querySelector('[data-outbox-list]');
    if (!list) {
      return;
    }

    const item = document.createElement('article');
    item.innerHTML = '<strong>Missing information follow-up: PO-IL-4001</strong><span>To: sam.rivera@example.com, central.sales@example.com</span><p>Follow-up reminder queued for missing site, vehicle, and production details.</p>';
    list.prepend(item);
    setCount('outbox', getCount('outbox') + 1);
    showView('outbox');
  }

  function updateJobDetail(key) {
    const detail = detailData[key];
    if (!detail) {
      return;
    }

    document.querySelector('[data-detail-title]').textContent = detail.title;
    document.querySelector('[data-detail-customer]').textContent = detail.customer;
    document.querySelector('[data-detail-status]').textContent = detail.status;
    document.querySelector('[data-detail-recommendation]').textContent = detail.recommendation;
    document.querySelector('[data-detail-materials]').textContent = detail.materials;
    document.querySelector('[data-detail-notes]').textContent = detail.notes;
  }

  navButtons.forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.target, true));
  });

  shortcutButtons.forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.targetShortcut, true));
  });

  document.querySelectorAll('[data-action="approve-job"]').forEach((button) => {
    button.addEventListener('click', approveJob);
  });

  document.querySelectorAll('[data-action="queue-reminder"]').forEach((button) => {
    button.addEventListener('click', queueReminder);
  });

  document.querySelectorAll('[data-job-detail]').forEach((button) => {
    button.addEventListener('click', () => updateJobDetail(button.dataset.jobDetail));
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

  const requestedView = window.location.hash.replace('#', '');
  if (requestedView && views.some((view) => view.dataset.view === requestedView)) {
    showView(requestedView);
  } else {
    showView('dashboard');
  }
});
