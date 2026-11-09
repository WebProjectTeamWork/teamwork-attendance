// Messages page: an inbox for professors and students, and group posts for professors.
// Uses the helpers from app.js (el, api, toast, fill, every, confirmDialog, svgIcon, renderHome).

const MAIL_POLL_MS = 20_000;
const PICKER_RESULTS = 6;

let setMessagesBadge = () => {};

function messagesButton() {
  const count = el("span", { class: "mail-count", hidden: true });
  const button = el(
    "button",
    {
      class: "btn secondary small mail-btn",
      "aria-label": "Messages",
      "aria-current": state.newsletter ? "page" : null,
      onclick: () => {
        state.newsletter = !state.newsletter;
        renderHome();
      },
    },
    svgIcon(ICONS.mail, 18),
    el("span", { class: "mail-btn-label" }, "Messages"),
    count,
  );
  setMessagesBadge = (unread) => {
    count.hidden = !unread;
    count.textContent = unread > 99 ? "99+" : unread;
    button.setAttribute("aria-label", unread ? `Messages, ${unread} unread` : "Messages");
  };
  refreshMessagesBadge();
  return button;
}

async function refreshMessagesBadge() {
  try {
    setMessagesBadge((await api("/messages/unread-count")).unread);
  } catch {
    /* the badge is a convenience; ignore failures */
  }
}

function mailWhen(iso) {
  const date = new Date(iso);
  return date.toDateString() === new Date().toDateString() ? formatTime(iso) : formatDate(iso);
}

const initials = (name) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");

// Romanian names: "Ștefan" should match a search for "stefan".
const foldText = (text) => text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

const studentCount = (count) => (count === 1 ? "1 student" : `${count} students`);

async function messagesPage(main) {
  const mail = { box: "inbox", threads: [], query: "", openId: null, shownCount: 0, contacts: null, hasDraft: () => false };

  const listHost = el("div", { class: "mail-threads" });
  const reader = el("section", { class: "mail-reader", "aria-label": "Conversation" });
  const root = el("div", { class: "mail", "data-pane": "list" });
  const search = el("input", {
    type: "text",
    placeholder: "Search messages",
    "aria-label": "Search messages",
    oninput: () => {
      mail.query = foldText(search.value.trim());
      drawList();
    },
  });

  const boxTabs = [
    ["inbox", "Inbox"],
    ["sent", "Sent"],
  ].map(([key, label]) =>
    el("button", { role: "tab", "data-box": key, "aria-selected": String(key === mail.box), onclick: () => switchBox(key) }, label),
  );

  root.append(el("div", { class: "mail-list" }, el("div", { class: "tabs", role: "tablist", "aria-label": "Mailbox" }, boxTabs), el("div", { class: "mail-search" }, search), listHost), reader);

  fill(main,
    el(
      "div",
      { class: "page-head" },
      el("h1", {}, "Messages"),
      el("button", { class: "btn", onclick: () => leaveDraft().then((ok) => ok && drawCompose()) }, "New message"),
    ),
    root,
  );

  function showPane(pane) {
    root.dataset.pane = pane;
  }

  async function leaveDraft() {
    if (!mail.hasDraft()) return true;
    return confirmDialog({ title: "Discard this draft?", body: "The text you wrote will be lost.", confirmLabel: "Discard draft", danger: true });
  }

  function drawEmptyReader() {
    mail.openId = null;
    mail.hasDraft = () => false;
    fill(reader, el("div", { class: "mail-placeholder" }, svgIcon(ICONS.mail, 32), el("p", {}, "Select a conversation to read it.")));
    showPane("list");
    drawList();
  }

  async function switchBox(box) {
    mail.box = box;
    boxTabs.forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.box === box)));
    await loadList();
  }

  async function loadList() {
    mail.threads = await api(`/messages/${mail.box}`);
    drawList();
  }

  function drawList() {
    const shown = mail.threads.filter((thread) => foldText(`${thread.counterpart} ${thread.subject} ${thread.preview}`).includes(mail.query));
    if (!shown.length) {
      const text = mail.query ? "No messages match your search." : mail.box === "inbox" ? "Messages sent to you appear here." : "Messages you start appear here.";
      fill(listHost, el("p", { class: "mail-list-empty muted small" }, text));
      return;
    }
    fill(listHost, el("ul", {}, shown.map(threadRow)));
  }

  function threadRow(thread) {
    const preview = thread.message_count > 1 ? `${thread.last_sender}: ${thread.preview}` : thread.preview;
    return el(
      "li",
      {},
      el(
        "button",
        {
          class: thread.unread ? "thread-row unread" : "thread-row",
          "aria-current": String(thread.id === mail.openId),
          onclick: () => leaveDraft().then((ok) => ok && openThread(thread.id)),
        },
        el(
          "span",
          { class: "thread-top" },
          el("span", { class: "thread-who" }, thread.is_group ? svgIcon(ICONS.group, 15) : null, el("span", {}, thread.counterpart)),
          el("time", { datetime: thread.last_at }, mailWhen(thread.last_at)),
        ),
        el("span", { class: "thread-subject" }, el("span", {}, thread.subject), thread.unread ? el("span", { class: "visually-hidden" }, `, ${thread.unread} unread`) : null),
        el("span", { class: "thread-preview" }, preview),
      ),
    );
  }

  async function openThread(id) {
    let detail;
    try {
      detail = await api(`/messages/threads/${id}`);
    } catch (error) {
      toast(error.message);
      return;
    }
    mail.openId = id;
    mail.shownCount = detail.messages.length;
    drawThread(detail);
    showPane("reader");
    const summary = mail.threads.find((thread) => thread.id === id);
    if (summary?.unread) {
      summary.unread = 0;
      await api(`/messages/threads/${id}/read`, { method: "POST" }).catch(() => {});
      refreshMessagesBadge();
    }
    drawList();
  }

  function backButton() {
    return el("button", { class: "btn link mail-back", onclick: () => leaveDraft().then((ok) => ok && drawEmptyReader()) }, "Back to messages");
  }

  function messageBlock(detail, message, chooseTarget) {
    const replyable = detail.can_reply && detail.is_group && detail.messages[0].mine && !message.mine;
    return el(
      "article",
      { class: message.is_group ? "letter group" : "letter" },
      el("span", { class: "letter-avatar", "aria-hidden": "true" }, initials(message.sender_name)),
      el(
        "div",
        { class: "letter-main" },
        el(
          "header",
          {},
          el("div", {}, el("strong", {}, message.mine ? "You" : message.sender_name), el("span", { class: "muted small" }, ` to ${message.to}`)),
          el("time", { class: "muted small", datetime: message.created_at }, `${formatDate(message.created_at)}, ${formatTime(message.created_at)}`),
        ),
        el("p", { class: "letter-body" }, message.body),
        message.recipient_count ? el("p", { class: "muted small letter-note" }, `Read by ${message.read_count} of ${studentCount(message.recipient_count)}`) : null,
        replyable ? el("button", { class: "btn link small", onclick: () => chooseTarget(message) }, `Reply to ${message.sender_name}`) : null,
      ),
    );
  }

  function drawThread(detail) {
    const scroller = el("div", { class: "mail-scroll" });
    const composer = el("div");
    const authorOfGroupPost = detail.is_group && detail.messages[0].mine;

    function defaultTarget() {
      const last = detail.messages[detail.messages.length - 1];
      if (authorOfGroupPost) return { id: detail.messages[0].id, text: `Your reply goes to everyone in ${detail.audience}.` };
      const other = [...detail.messages].reverse().find((message) => !message.mine);
      const person = other ? other.sender_name : last.to;
      return { id: last.id, text: detail.is_group ? `Your reply goes only to ${person}.` : `Replying to ${person}.` };
    }

    let target = defaultTarget();

    function chooseTarget(message) {
      target = { id: message.id, text: `Only ${message.sender_name} will see your reply.`, chosen: true };
      drawComposer();
      composer.querySelector("textarea").focus();
    }

    function drawComposer() {
      if (!detail.can_reply) {
        mail.hasDraft = () => false;
        fill(composer, el("p", { class: "mail-closed muted small" }, "You can't reply because you no longer share a class with this person."));
        return;
      }
      const previous = composer.querySelector("textarea")?.value || "";
      const box = el("textarea", { rows: 3, maxlength: 5000, placeholder: "Write a reply", "aria-label": "Reply" });
      box.value = previous;
      const sendButton = el("button", { class: "btn", type: "submit" }, "Send reply");
      mail.hasDraft = () => box.value.trim() !== "";

      async function send(event) {
        event?.preventDefault();
        const body = box.value.trim();
        if (!body) return box.focus();
        sendButton.disabled = true;
        try {
          await api(`/messages/${target.id}/reply`, { method: "POST", json: { body } });
          box.value = "";
          toast("Reply sent");
          await openThread(detail.id);
          await loadList();
        } catch (error) {
          toast(error.message);
          sendButton.disabled = false;
        }
      }

      box.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) send(event);
      });

      fill(composer,
        el(
          "form",
          { class: "mail-reply", onsubmit: send },
          el(
            "p",
            { class: "muted small" },
            target.text,
            target.chosen
              ? el("button", { class: "btn link small", type: "button", onclick: () => ((target = defaultTarget()), drawComposer()) }, `Reply to the whole group instead`)
              : null,
          ),
          box,
          el("div", { class: "mail-actions" }, sendButton),
        ),
      );
    }

    fill(scroller, detail.messages.map((message) => messageBlock(detail, message, chooseTarget)));
    fill(reader,
      el(
        "header",
        { class: "mail-head" },
        backButton(),
        el("h2", {}, detail.subject),
        detail.is_group ? el("p", { class: "muted small mail-audience" }, svgIcon(ICONS.group, 15), detail.audience) : null,
      ),
      scroller,
      composer,
    );
    drawComposer();
    scroller.scrollTop = scroller.scrollHeight;
  }

  function personPicker(people) {
    let chosen = people.length === 1 ? people[0] : null;
    const host = el("div", { class: "picker" });
    const results = el("ul", { class: "picker-results" });
    const input = el("input", { type: "text", id: "mail-person", placeholder: "Search by name or class", autocomplete: "off", oninput: drawResults });

    function drawResults() {
      const term = foldText(input.value.trim());
      const matches = people.filter((person) => foldText(`${person.name} ${person.classes.join(" ")}`).includes(term));
      const shown = matches.slice(0, PICKER_RESULTS);
      fill(results,
        shown.length
          ? shown.map((person) =>
              el(
                "li",
                {},
                el(
                  "button",
                  { type: "button", onclick: () => pick(person) },
                  el("span", { class: "letter-avatar", "aria-hidden": "true" }, initials(person.name)),
                  el("span", {}, el("strong", {}, person.name), el("span", { class: "muted small" }, person.classes.join(", "))),
                ),
              ),
            )
          : el("li", { class: "muted small picker-none" }, "Nobody in your classes matches that name."),
        matches.length > shown.length ? el("li", { class: "muted small picker-none" }, `${matches.length - shown.length} more. Keep typing to narrow the list.`) : null,
      );
    }

    function pick(person) {
      chosen = person;
      draw();
    }

    function draw() {
      if (chosen) {
        fill(host,
          el(
            "div",
            { class: "picker-chosen" },
            el("span", { class: "letter-avatar", "aria-hidden": "true" }, initials(chosen.name)),
            el("span", {}, el("strong", {}, chosen.name), el("span", { class: "muted small" }, chosen.classes.join(", "))),
            people.length > 1 ? el("button", { class: "btn link small", type: "button", onclick: () => pick(null) }, "Change") : null,
          ),
        );
        return;
      }
      input.value = "";
      drawResults();
      fill(host, input, results);
    }

    draw();
    return { node: host, value: () => chosen?.id || null, name: () => chosen?.name, touched: () => input.value !== "" };
  }

  function groupPicker(groups) {
    const boxes = groups.map((group) => ({
      group,
      input: el("input", { type: "checkbox", disabled: group.student_ids.length === 0, onchange: update }),
    }));
    const reach = el("p", { class: "mail-reach", role: "status" });

    function selected() {
      return boxes.filter((box) => box.input.checked);
    }

    function reachCount() {
      return new Set(selected().flatMap((box) => box.group.student_ids)).size;
    }

    function update() {
      const count = reachCount();
      reach.textContent = count ? `${studentCount(count)} will receive this message.` : "Choose at least one group.";
      const available = boxes.filter((box) => !box.input.disabled);
      allButton.textContent = available.length && available.every((box) => box.input.checked) ? "Clear all" : "Select all";
    }

    const allButton = el("button", {
      class: "btn link small",
      type: "button",
      onclick: () => {
        const available = boxes.filter((box) => !box.input.disabled);
        const check = !available.every((box) => box.input.checked);
        available.forEach((box) => (box.input.checked = check));
        update();
      },
    });

    const node = el(
      "fieldset",
      { class: "mail-groups" },
      el("legend", {}, "Groups"),
      el(
        "div",
        { class: "mail-group-list" },
        boxes.map(({ group, input }) =>
          el("label", { class: input.disabled ? "mail-group disabled" : "mail-group" }, input, el("span", {}, group.name), el("span", { class: "muted small" }, group.student_ids.length ? studentCount(group.student_ids.length) : "No students yet")),
        ),
      ),
      el("div", { class: "row spread" }, reach, allButton),
    );
    update();
    return { node, value: () => selected().map((box) => box.group.id), count: reachCount };
  }

  async function drawCompose() {
    try {
      mail.contacts = await api("/messages/contacts");
    } catch (error) {
      toast(error.message);
      return;
    }
    mail.openId = null;
    drawList();
    showPane("reader");
    const { people, groups } = mail.contacts;
    const header = el("header", { class: "mail-head" }, backButton(), el("h2", {}, "New message"));

    if (!people.length && !groups.length) {
      mail.hasDraft = () => false;
      fill(reader, header, el("div", { class: "mail-placeholder" }, el("p", {}, state.user.role === "professor" ? "Create a class and add students before sending messages." : "You can write to your professors once you are added to a class.")));
      return;
    }

    const isProfessor = state.user.role === "professor";
    const picker = personPicker(people);
    const groupsPicker = isProfessor ? groupPicker(groups) : null;
    const subject = el("input", { type: "text", maxlength: 150, required: true });
    const body = el("textarea", { rows: 8, maxlength: 5000, required: true });
    const error = el("p", { class: "error-text", role: "alert" });
    const sendButton = el("button", { class: "btn", type: "submit" }, "Send message");
    let toGroup = isProfessor && groups.length > 0;
    const audienceHost = el("div");
    mail.hasDraft = () => subject.value.trim() !== "" || body.value.trim() !== "";

    function drawAudience() {
      fill(audienceHost,
        toGroup
          ? groupsPicker.node
          : el("div", { class: "field" }, el("label", { for: "mail-person", class: "mail-label" }, isProfessor ? "Student" : "Professor"), picker.node),
      );
    }

    const audienceChoice = isProfessor
      ? el(
          "fieldset",
          { class: "choice-group" },
          el("legend", {}, "Send to"),
          [
            [true, "Whole groups"],
            [false, "One student"],
          ].map(([value, label]) =>
            el(
              "label",
              { class: "choice" },
              el("input", {
                type: "radio",
                name: "mail-audience",
                checked: value === toGroup,
                onchange: () => {
                  toGroup = value;
                  error.textContent = "";
                  drawAudience();
                },
              }),
              el("span", {}, label),
            ),
          ),
        )
      : null;

    async function send(event) {
      event.preventDefault();
      error.textContent = "";
      const audience = toGroup ? { section_ids: groupsPicker.value() } : { recipient_id: picker.value() };
      if (toGroup ? !audience.section_ids.length : !audience.recipient_id) {
        error.textContent = toGroup ? "Choose at least one group." : `Choose a ${isProfessor ? "student" : "professor"}.`;
        return;
      }
      sendButton.disabled = true;
      try {
        const sent = await api("/messages", { method: "POST", json: { ...audience, subject: subject.value, body: body.value } });
        mail.hasDraft = () => false;
        toast(toGroup ? `Sent to ${studentCount(sent.recipient_count)}` : `Sent to ${picker.name()}`);
        await switchBox("sent");
        await openThread(sent.thread_id);
      } catch (failure) {
        error.textContent = failure.message;
        sendButton.disabled = false;
      }
    }

    drawAudience();
    fill(reader,
      header,
      el(
        "form",
        { class: "mail-compose", onsubmit: send },
        audienceChoice,
        audienceHost,
        el("label", { class: "field" }, el("span", {}, "Subject"), subject),
        el("label", { class: "field" }, el("span", {}, "Message"), body),
        error,
        el("div", { class: "mail-actions" }, el("button", { class: "btn secondary", type: "button", onclick: () => leaveDraft().then((ok) => ok && drawEmptyReader()) }, "Cancel"), sendButton),
      ),
    );
    (toGroup ? subject : picker.value() ? subject : audienceHost.querySelector("input"))?.focus();
  }

  async function poll() {
    await loadList();
    refreshMessagesBadge();
    if (!mail.openId || mail.hasDraft()) return;
    const open = mail.threads.find((thread) => thread.id === mail.openId);
    if (open && open.message_count !== mail.shownCount) await openThread(mail.openId);
  }

  drawEmptyReader();
  await loadList();
  every(MAIL_POLL_MS, () => poll().catch(() => {}));
}
