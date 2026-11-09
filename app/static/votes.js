// Votes: a professor offers dates or written options to some of their classes, students pick, the professor sets the result.
// Uses the helpers from app.js (el, api, toast, fill, every, confirmDialog, formatDate, formatTime, fullName, state).

const VOTE_POLL_MS = 15_000;

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
const optionMain = (option) => (option.starts_at ? `${formatDate(option.starts_at)}, ${formatTime(option.starts_at)}` : option.label);
const optionText = (option) => (option.note ? `${optionMain(option)} (${option.note})` : optionMain(option));
const isDateVote = (vote) => vote.kind !== "text";

// The option with the most votes; on a tie the first one, which is the earliest date or the first option written.
function leadingOption(vote) {
  return vote.options.reduce((best, option) => ((option.votes || 0) > (best.votes || 0) ? option : best), vote.options[0]);
}

// datetime-local wants local time without an offset.
function localInputValue(date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function groupTags(vote) {
  return el("span", { class: "all-student-classes" }, vote.groups.map((group) => el("span", { class: "class-tag" }, group.name)));
}

function answeredBar(vote) {
  const share = vote.eligible ? (vote.voted * 100) / vote.eligible : 0;
  return el(
    "div",
    { class: "vote-progress" },
    el("span", { class: "muted small" }, `${vote.voted} of ${plural(vote.eligible, "student", "students")} answered`),
    el("div", { class: "vote-bar thin", role: "img", "aria-label": `${Math.round(share)}% answered` }, el("span", { style: `width:${share}%` })),
  );
}

// Totals per option. Bars are relative to the number of voters, so with several picks each they can add up to more than 100%.
function optionBars(vote, { showVoters = false } = {}) {
  const voters = Math.max(vote.voted || 0, 1);
  const leader = vote.status === "open" && vote.voted ? leadingOption(vote) : null;
  return el(
    "ul",
    { class: "list" },
    vote.options.map((option) => {
      const chosen = option.id === vote.chosen_option_id;
      const tag = chosen ? el("span", { class: "vote-tag chosen" }, isDateVote(vote) ? "Date set" : "Result") : option === leader ? el("span", { class: "vote-tag" }, "Leading") : null;
      return el(
        "li",
        { class: chosen ? "vote-option chosen" : "vote-option" },
        el("div", { class: "row spread" }, el("span", { class: "row" }, el("strong", {}, optionText(option)), tag), option.votes === null ? null : el("span", { class: "muted small" }, plural(option.votes, "vote", "votes"))),
        option.votes === null ? null : el("div", { class: "vote-bar" }, el("span", { style: `width:${(option.votes * 100) / voters}%` })),
        showVoters && option.voters?.length ? el("p", { class: "muted small" }, option.voters.map(fullName).join(", ")) : null,
      );
    }),
  );
}

/* ---------- Professor ---------- */

let voteTimer = null;

function stopVoteTimer() {
  if (voteTimer === null) return;
  clearInterval(voteTimer);
  state.timers.delete(voteTimer);
  voteTimer = null;
}

async function votesPage(main, openId = null) {
  stopVoteTimer();
  const votes = await api("/votes");
  const shown = votes.find((vote) => vote.id === openId);
  if (shown) return voteDetail(main, shown);

  const formHost = el("div");
  const newButton = el("button", { class: "btn", onclick: () => showForm() }, "New vote");
  function showForm() {
    newButton.hidden = true;
    fill(formHost, voteForm(main, () => ((newButton.hidden = false), fill(formHost))));
    formHost.querySelector("input")?.focus();
  }

  const open = votes.filter((vote) => vote.status === "open");
  const decided = votes.filter((vote) => vote.status !== "open");
  const row = (vote) => {
    const chosen = vote.options.find((option) => option.id === vote.chosen_option_id);
    return el(
      "li",
      { class: "vote-row" },
      el(
        "div",
        {},
        el("strong", {}, vote.title),
        chosen ? el("span", { class: "small" }, `${isDateVote(vote) ? "Date" : "Result"}: ${optionText(chosen)}`) : answeredBar(vote),
        groupTags(vote),
      ),
      el("button", { class: "btn secondary small", onclick: () => votesPage(main, vote.id) }, chosen ? "View" : "Results"),
    );
  };

  fill(main,
    el(
      "div",
      { class: "page-head" },
      el("div", {}, el("h1", {}, "Votes"), el("p", { class: "muted" }, "Ask your classes to pick a date, a topic, or anything else.")),
      newButton,
    ),
    formHost,
    open.length ? el("div", { class: "section-block" }, el("h2", {}, "Open"), el("ul", { class: "list" }, open.map(row))) : null,
    decided.length ? el("div", { class: "section-block" }, el("h2", {}, "Decided"), el("ul", { class: "list" }, decided.map(row))) : null,
    votes.length ? null : el("div", { class: "empty", style: "margin-top:20px;max-width:520px" }, el("strong", {}, "No votes yet"), "Students of the classes you pick get a notification and answer from their page."),
  );
  if (!votes.length && state.sections.length) showForm();
}

function voteForm(main, onCancel) {
  const errorBox = el("p", { class: "error-text", role: "alert" });
  const classError = el("p", { class: "error-text", role: "alert" });
  let kind = "date";

  /* Options: one list per kind, so switching back and forth keeps what was typed. */
  const dateRows = el("div", { class: "vote-rows" });
  const textRows = el("div", { class: "vote-rows", hidden: true });
  const rowsOf = () => (kind === "date" ? dateRows : textRows);

  function refreshRemove() {
    for (const rows of [dateRows, textRows]) {
      for (const button of rows.querySelectorAll(".vote-remove")) button.disabled = rows.children.length <= 2;
    }
  }

  function addDateRow(when) {
    const previous = [...dateRows.querySelectorAll("input[name=starts_at]")].at(-1)?.value;
    const value = when || (previous ? new Date(new Date(previous).getTime() + 86400000) : null);
    const row = el(
      "div",
      { class: "vote-option-row" },
      el("input", { type: "datetime-local", name: "starts_at", required: kind === "date", min: localInputValue(new Date()), value: value ? localInputValue(value) : "", "aria-label": "Date and time" }),
      el("input", { type: "text", name: "note", maxlength: 200, placeholder: "Room or note (optional)", "aria-label": "Note" }),
      el("button", { class: "btn secondary small vote-remove", type: "button", "aria-label": "Remove this date", onclick: () => (row.remove(), refreshRemove()) }, "Remove"),
    );
    dateRows.append(row);
    refreshRemove();
    return row;
  }

  function addTextRow(text = "") {
    const label = el("input", { type: "text", name: "label", required: kind === "text", maxlength: 200, value: text, placeholder: `Option ${textRows.children.length + 1}`, "aria-label": "Option" });
    // Enter moves on to the next option instead of starting the vote.
    label.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const next = row.nextElementSibling?.querySelector("input[name=label]");
      (next || addTextRow().querySelector("input[name=label]")).focus();
    });
    const row = el(
      "div",
      { class: "vote-option-row" },
      label,
      el("input", { type: "text", name: "note", maxlength: 200, placeholder: "Note (optional)", "aria-label": "Note" }),
      el("button", { class: "btn secondary small vote-remove", type: "button", "aria-label": "Remove this option", onclick: () => (row.remove(), refreshRemove()) }, "Remove"),
    );
    textRows.append(row);
    refreshRemove();
    return row;
  }

  const tomorrow = new Date(Date.now() + 86400000);
  tomorrow.setHours(10, 0, 0, 0);
  addDateRow(tomorrow);
  addDateRow();
  addTextRow();
  addTextRow();

  const addButton = el("button", { class: "btn secondary small", type: "button", onclick: () => (kind === "date" ? addDateRow() : addTextRow()).querySelector("input").focus() }, "Add a date");
  const yesNo = el(
    "button",
    {
      class: "btn link small",
      type: "button",
      hidden: true,
      onclick: () => {
        textRows.replaceChildren();
        addTextRow("Yes");
        addTextRow("No");
      },
    },
    "Fill in Yes and No",
  );
  const kindHint = el("p", { class: "muted small" }, "For an exam, a colloquium or a make-up lab.");
  const optionsLabel = el("span", {}, "Dates to choose from");
  const multipleHint = el("p", { class: "muted small" }, "Several is handy for finding the dates that suit most people.");

  function setKind(value) {
    kind = value;
    dateRows.hidden = kind !== "date";
    textRows.hidden = kind !== "text";
    yesNo.hidden = kind !== "text";
    addButton.textContent = kind === "date" ? "Add a date" : "Add an option";
    optionsLabel.textContent = kind === "date" ? "Dates to choose from" : "Options to choose from";
    multipleHint.textContent = kind === "date" ? "Several is handy for finding the dates that suit most people." : "Several lets students tick everything they would be happy with.";
    kindHint.textContent = kind === "date" ? "For an exam, a colloquium or a make-up lab." : "For example a project topic, a team, or a yes/no question.";
    // Only the visible list is checked by the browser before submitting.
    dateRows.querySelectorAll("input[name=starts_at]").forEach((input) => (input.required = kind === "date"));
    textRows.querySelectorAll("input[name=label]").forEach((input) => (input.required = kind === "text"));
  }

  /* Classes */
  const classBoxes = state.sections.map((section) =>
    el(
      "label",
      { class: "toggle" },
      el("input", { type: "checkbox", name: "section", value: section.id, onchange: () => (classError.textContent = "") }),
      section.name,
      el("span", { class: "muted small" }, plural(section.student_count, "student", "students")),
    ),
  );
  const selectAll =
    state.sections.length > 1
      ? el("button", { class: "btn link small", type: "button", onclick: () => classBoxes.forEach((box) => (box.querySelector("input").checked = true)) }, "Select all")
      : null;

  const radios = (name, legend, hint, options, checked, onchange) =>
    el(
      "fieldset",
      { class: "vote-setting" },
      el("legend", {}, legend),
      el(
        "div",
        { class: "vote-choices" },
        options.map(([value, label]) =>
          el("label", { class: "choice" }, el("input", { type: "radio", name, value, checked: value === checked, onchange: onchange && (() => onchange(value)) }), el("span", {}, label)),
        ),
      ),
      hint instanceof Node ? hint : hint ? el("p", { class: "muted small" }, hint) : null,
    );

  async function submit(event) {
    event.preventDefault();
    const form = event.target;
    const data = new FormData(form);
    const sectionIds = data.getAll("section");
    errorBox.textContent = "";
    if (!sectionIds.length) {
      classError.textContent = "Pick at least one class.";
      classBoxes[0]?.querySelector("input").focus();
      return;
    }
    const rows = [...rowsOf().children];
    const notes = rows.map((row) => row.querySelector("input[name=note]").value.trim() || null);
    let options;
    if (kind === "date") {
      options = rows.map((row, index) => ({ starts_at: new Date(row.querySelector("input[name=starts_at]").value).toISOString(), note: notes[index] }));
    } else {
      options = rows.map((row, index) => ({ label: row.querySelector("input[name=label]").value.trim(), note: notes[index] }));
      const seen = new Set(options.map((option) => option.label.toLowerCase()));
      if (seen.size !== options.length) return (errorBox.textContent = "Two options have the same text.");
    }
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const vote = await api("/votes", {
        method: "POST",
        json: {
          title: data.get("title"),
          description: data.get("description") || null,
          kind,
          section_ids: sectionIds,
          options,
          allow_multiple: data.get("multiple") === "yes",
          anonymous: data.get("anonymous") === "yes",
          results_visibility: data.get("visibility"),
        },
      });
      toast(`Vote started. ${plural(vote.eligible, "student was", "students were")} notified.`);
      votesPage(main, vote.id);
    } catch (error) {
      errorBox.textContent = error.message;
      button.disabled = false;
    }
  }

  const form = el(
    "form",
    { class: "vote-form", onsubmit: submit },
    el("h2", { id: "vote-form-title" }, "New vote"),
    el("label", { class: "field" }, el("span", {}, "What is the vote about?"), el("input", { type: "text", name: "title", required: true, maxlength: 200, placeholder: "Databases exam" })),
    el(
      "fieldset",
      { class: "vote-classes" },
      el("legend", {}, "Classes that vote ", el("span", { class: "hint" }, "(pick at least one)")),
      state.sections.length ? el("div", { class: "vote-class-list" }, classBoxes) : el("p", { class: "muted" }, "Create a class first."),
      selectAll,
      classError,
    ),
    el(
      "div",
      { class: "vote-kind" },
      radios("kind", "Students choose between", null, [["date", "Dates"], ["text", "Something else"]], "date", setKind),
      kindHint,
    ),
    el("div", { class: "field" }, optionsLabel, dateRows, textRows, el("div", { class: "row" }, addButton, yesNo)),
    radios("multiple", "Each student picks", multipleHint, [["no", "One"], ["yes", "Several"]], "no"),
    radios("anonymous", "Names", "Anonymous still shows you who hasn't answered.", [["no", "Show me who picked what"], ["yes", "Anonymous"]], "no"),
    radios("visibility", "Students see the results", "Results shown early can sway the students who answer later.", [["live", "After they vote"], ["after_close", "When the vote closes"], ["professor_only", "Never"]], "after_close"),
    el("label", { class: "field" }, el("span", {}, "Details ", el("span", { class: "hint" }, "(optional)")), el("textarea", { name: "description", maxlength: 2000, placeholder: "Anything students should know before they answer" })),
    errorBox,
    el("div", { class: "row" }, el("button", { class: "btn", type: "submit" }, "Start vote"), el("button", { class: "btn secondary", type: "button", onclick: onCancel }, "Cancel"), el("span", { class: "muted small" }, "Students in these classes get a notification.")),
  );
  return el("section", { class: "panel", style: "margin-top:20px", "aria-labelledby": "vote-form-title" }, form);
}

function voteDetail(main, vote) {
  const dateVote = isDateVote(vote);
  const settings = [
    vote.allow_multiple ? "Students pick several" : "One pick each",
    vote.anonymous ? "anonymous" : "names shown",
    { live: "students see results after voting", after_close: "students see results when it closes", professor_only: "only you see the results" }[vote.results_visibility],
  ].join(", ");
  const resultsHost = el("div", {}, optionBars(vote, { showVoters: true }));
  const progressHost = el("div", {}, answeredBar(vote));
  const missingHost = el("div");
  let closePanel = null;

  function drawMissing(current) {
    fill(missingHost,
      current.not_voted?.length
        ? el(
            "details",
            { style: "margin-top:16px" },
            el("summary", {}, `${plural(current.not_voted.length, "student hasn't", "students haven't")} answered`),
            el("p", { class: "muted small", style: "margin-top:8px" }, current.not_voted.map(fullName).join(", ")),
          )
        : null,
    );
  }
  drawMissing(vote);

  if (vote.status === "open") {
    const leader = leadingOption(vote);
    const select = el(
      "select",
      { "aria-label": dateVote ? "Date to set" : "Result to set" },
      vote.options.map((option) => el("option", { value: option.id, selected: option.id === leader.id }, `${optionText(option)} (${plural(option.votes, "vote", "votes")})`)),
    );
    let picked = false;
    select.addEventListener("change", () => (picked = true));
    const closeHint = el("p", { class: "muted small" });
    const drawCloseHint = (current) =>
      (closeHint.textContent = current.voted ? "The most voted option is selected. You can pick another one." : "Nobody has answered yet. You can still close the vote with any option.");
    drawCloseHint(vote);

    async function closeVote() {
      const option = vote.options.find((item) => item.id === select.value);
      const ok = await confirmDialog({
        title: dateVote ? "Set this date?" : "Set this result?",
        body: `${optionText(option)}. The vote closes, students can't change their choice any more, and everyone in the classes is told.`,
        confirmLabel: "Close the vote",
      });
      if (!ok) return;
      try {
        await api(`/votes/${vote.id}/close`, { method: "POST", json: { option_id: option.id } });
        toast("Vote closed. Students have been notified.");
        votesPage(main, vote.id);
      } catch (error) {
        toast(error.message);
      }
    }

    closePanel = el(
      "section",
      { class: "panel vote-close", style: "margin-top:20px" },
      el("h2", {}, dateVote ? "Set the date" : "Set the result"),
      closeHint,
      el("div", { class: "row" }, select, el("button", { class: "btn", onclick: closeVote }, "Close the vote")),
    );

    // New answers show up while the page is open.
    voteTimer = every(VOTE_POLL_MS, async () => {
      try {
        const fresh = await api(`/votes/${vote.id}`);
        fill(resultsHost, optionBars(fresh, { showVoters: true }));
        fill(progressHost, answeredBar(fresh));
        drawMissing(fresh);
        drawCloseHint(fresh);
        if (!picked && document.activeElement !== select) {
          const best = leadingOption(fresh);
          [...select.options].forEach((item, index) => {
            item.textContent = `${optionText(fresh.options[index])} (${plural(fresh.options[index].votes, "vote", "votes")})`;
          });
          select.value = best.id;
        }
        vote.options = fresh.options;
      } catch {
        /* keep the last results */
      }
    });
  }

  async function remove() {
    const ok = await confirmDialog({
      title: "Delete this vote?",
      body: vote.status === "open" ? "The answers are deleted and students are told the vote was cancelled." : "The vote and its results are deleted.",
      confirmLabel: "Delete vote",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/votes/${vote.id}`, { method: "DELETE" });
      toast("Vote deleted.");
      votesPage(main);
    } catch (error) {
      toast(error.message);
    }
  }

  const chosen = vote.options.find((option) => option.id === vote.chosen_option_id);
  fill(main,
    el("div", {}, el("button", { class: "btn link", onclick: () => votesPage(main) }, "Back to votes")),
    el(
      "div",
      { class: "page-head", style: "margin-top:12px" },
      el("div", { class: "stack" }, el("h1", {}, vote.title), groupTags(vote), el("p", { class: "muted small" }, `${settings}.`)),
    ),
    vote.description ? el("p", { style: "margin-bottom:16px" }, vote.description) : null,
    chosen ? el("p", { class: "vote-result" }, `${dateVote ? "Date" : "Result"}: `, el("strong", {}, optionText(chosen))) : progressHost,
    el("div", { style: "margin-top:16px" }, resultsHost),
    missingHost,
    closePanel,
    el("div", { style: "margin-top:32px" }, el("button", { class: "btn danger small", onclick: remove }, "Delete vote")),
  );
}

/* ---------- Student ---------- */

// Draws the student's votes into host and returns a refresh function for the page's poll.
function studentVotes(host) {
  let editing = false;

  async function load() {
    if (editing) return;
    const votes = await api("/votes");
    if (editing) return;
    if (!votes.length) return fill(host);
    const open = votes.filter((vote) => vote.status === "open");
    const decided = votes.filter((vote) => vote.status !== "open");
    const waiting = open.filter((vote) => !vote.my_choice.length).length;
    fill(host,
      el("div", { class: "row" }, el("h2", {}, "Votes"), waiting ? el("span", { class: "live-pill" }, `${waiting} waiting for you`) : null),
      open.length ? el("div", { class: "stack", style: "margin-top:12px" }, open.map(openVote)) : el("p", { class: "muted small" }, "No open votes right now."),
      decided.length
        ? el("details", { style: "margin-top:16px" }, el("summary", {}, `Decided (${decided.length})`), el("div", { class: "stack", style: "margin-top:12px" }, decided.map(decidedVote)))
        : null,
    );
  }

  function header(vote) {
    return el(
      "div",
      { class: "stack" },
      el("div", { class: "row spread" }, el("h3", {}, vote.title), groupTags(vote)),
      vote.description ? el("p", {}, vote.description) : null,
    );
  }

  function privacyLine(vote) {
    const who = vote.anonymous ? "Anonymous: your professor sees only the totals." : "Your professor sees what you pick.";
    const when = {
      live: "You see the results after you answer.",
      after_close: "Results show when the vote closes.",
      professor_only: "Only your professor sees the results.",
    }[vote.results_visibility];
    return el("p", { class: "muted small" }, `${who} ${when}`);
  }

  function openVote(vote) {
    const type = vote.allow_multiple ? "checkbox" : "radio";
    const saved = new Set(vote.my_choice);
    const name = `vote-${vote.id}`;
    const errorBox = el("p", { class: "error-text", role: "alert" });
    const button = el("button", { class: "btn", type: "submit", disabled: true }, saved.size ? "Save changes" : "Save my choice");
    const voters = Math.max(vote.voted || 0, 1);

    function changed(form) {
      const now = [...form.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
      return now.length !== saved.size || now.some((id) => !saved.has(id));
    }

    async function save(event) {
      event.preventDefault();
      const optionIds = [...event.target.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
      if (!optionIds.length) return (errorBox.textContent = "Tick at least one option.");
      button.disabled = true;
      errorBox.textContent = "";
      try {
        await api(`/votes/${vote.id}/choice`, { method: "PUT", json: { option_ids: optionIds } });
        toast("Saved. You can change it until the vote closes.");
        editing = false;
        load();
      } catch (error) {
        errorBox.textContent = error.message;
        button.disabled = false;
      }
    }

    const form = el(
      "form",
      {
        class: "panel vote-card",
        onsubmit: save,
        onchange: (event) => {
          editing = changed(event.currentTarget);
          button.disabled = !editing;
        },
      },
      header(vote),
      el(
        "fieldset",
        { class: "vote-picks" },
        el("legend", { class: "small" }, vote.allow_multiple ? "Tick every option that suits you" : "Pick one"),
        vote.options.map((option) =>
          el(
            "label",
            { class: "vote-pick" },
            el("input", { type, name, value: option.id, checked: saved.has(option.id) }),
            el("span", { class: "vote-pick-text" }, optionText(option)),
            option.votes === null
              ? null
              : el("span", { class: "vote-pick-count muted small" }, plural(option.votes, "vote", "votes"), el("span", { class: "vote-bar thin" }, el("span", { style: `width:${(option.votes * 100) / voters}%` }))),
          ),
        ),
      ),
      privacyLine(vote),
      errorBox,
      el("div", { class: "row" }, button, saved.size ? el("span", { class: "muted small" }, "You've answered. You can change it until the vote closes.") : null),
    );
    return form;
  }

  function decidedVote(vote) {
    const chosen = vote.options.find((option) => option.id === vote.chosen_option_id);
    return el(
      "div",
      { class: "panel" },
      header(vote),
      el("p", { class: "vote-result" }, `${isDateVote(vote) ? "Date" : "Result"}: `, el("strong", {}, chosen ? optionText(chosen) : "not set")),
      vote.results_visible ? el("div", { style: "margin-top:12px" }, optionBars(vote)) : null,
    );
  }

  load().catch(() => {});
  return () => load().catch(() => {});
}
