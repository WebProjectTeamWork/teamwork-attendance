// Votes: a professor offers dates to some of their classes, students pick, the professor sets the date.
// Uses the helpers from app.js (el, api, toast, fill, confirmDialog, formatDate, formatTime, fullName, renderProfessor).

const VISIBILITY_LABEL = {
  live: "after they vote",
  after_close: "after the vote closes",
  professor_only: "never, only you see them",
};

const voteWhen = (option) => `${formatDate(option.starts_at)}, ${formatTime(option.starts_at)}`;
const optionText = (option) => (option.note ? `${voteWhen(option)} (${option.note})` : voteWhen(option));

// The option with the most votes; options come sorted by date, so a tie keeps the earliest.
function leadingOption(vote) {
  return vote.options.reduce((best, option) => ((option.votes || 0) > (best.votes || 0) ? option : best), vote.options[0]);
}

// datetime-local wants local time without an offset.
function localInputValue(date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

function optionBars(vote, { showVoters = false } = {}) {
  const total = Math.max(vote.voted || 0, 1);
  return el(
    "ul",
    { class: "list" },
    vote.options.map((option) => {
      const chosen = option.id === vote.chosen_option_id;
      return el(
        "li",
        { class: chosen ? "vote-option chosen" : "vote-option" },
        el(
          "div",
          { class: "row spread" },
          el("strong", {}, optionText(option)),
          el("span", { class: "muted small" }, chosen ? "Date set" : null, option.votes === null ? null : `${chosen ? ", " : ""}${option.votes} ${option.votes === 1 ? "vote" : "votes"}`),
        ),
        option.votes === null
          ? null
          : el("div", { class: "vote-bar", role: "img", "aria-label": `${option.votes} of ${vote.voted} voters` }, el("span", { style: `width:${(option.votes * 100) / total}%` })),
        showVoters && option.voters?.length ? el("p", { class: "muted small" }, option.voters.map(fullName).join(", ")) : null,
      );
    }),
  );
}

/* ---------- Professor ---------- */

async function votesPage(main, openId = null) {
  const votes = await api("/votes");
  const open = votes.find((vote) => vote.id === openId);
  if (open) return voteDetail(main, open);

  const formHost = el("div");
  const newButton = el("button", { class: "btn", onclick: () => showForm() }, "New vote");

  function showForm() {
    newButton.hidden = true;
    fill(formHost, voteForm(main, () => ((newButton.hidden = false), fill(formHost))));
  }

  fill(main,
    el(
      "div",
      { class: "page-head" },
      el("div", {}, el("h1", {}, "Votes"), el("p", { class: "muted" }, "Let your classes pick a date, for example for an exam or a make-up lab.")),
      newButton,
    ),
    formHost,
    votes.length
      ? el(
          "ul",
          { class: "list", style: "margin-top:20px" },
          votes.map((vote) => {
            const chosen = vote.options.find((option) => option.id === vote.chosen_option_id);
            return el(
              "li",
              { class: "vote-row" },
              el(
                "div",
                {},
                el("div", { class: "row" }, el("strong", {}, vote.title), el("span", { class: `status ${vote.status === "open" ? "open" : "approved"}` }, vote.status === "open" ? "Open" : "Date set")),
                el("p", { class: "muted small" }, chosen ? `${optionText(chosen)}. ` : "", `${vote.voted} of ${vote.eligible} answered.`),
                el("span", { class: "all-student-classes" }, vote.groups.map((group) => el("span", { class: "class-tag" }, group.name))),
              ),
              el("button", { class: "btn secondary small", onclick: () => votesPage(main, vote.id) }, "Open"),
            );
          }),
        )
      : el("div", { class: "empty", style: "margin-top:20px;max-width:520px" }, el("strong", {}, "No votes yet"), "Start one with New vote. Students of the classes you pick get a notification."),
  );
  if (!votes.length && state.sections.length) showForm();
}

function voteForm(main, onCancel) {
  const errorBox = el("p", { class: "error-text", role: "alert" });
  const dates = el("div", { class: "vote-dates" });
  const tomorrow = new Date(Date.now() + 86400000);
  tomorrow.setHours(10, 0, 0, 0);

  function addDate(when) {
    const row = el(
      "div",
      { class: "vote-date" },
      el("input", { type: "datetime-local", name: "starts_at", required: true, min: localInputValue(new Date()), value: when ? localInputValue(when) : "", "aria-label": "Date and time" }),
      el("input", { type: "text", name: "note", maxlength: 200, placeholder: "Note, for example the room (optional)", "aria-label": "Note" }),
      el("button", { class: "btn secondary small", type: "button", onclick: () => dates.children.length > 2 ? row.remove() : toast("A vote needs at least two dates") }, "Remove"),
    );
    dates.append(row);
  }
  addDate(tomorrow);
  addDate(new Date(tomorrow.getTime() + 86400000));

  const radios = (name, legend, options, checked) =>
    el(
      "fieldset",
      { class: "choice-group" },
      el("legend", {}, legend),
      options.map(([value, label]) => el("label", { class: "choice" }, el("input", { type: "radio", name, value, checked: value === checked }), el("span", {}, label))),
    );

  async function submit(event) {
    event.preventDefault();
    const form = event.target;
    const data = new FormData(form);
    const sectionIds = data.getAll("section");
    if (!sectionIds.length) return (errorBox.textContent = "Pick at least one class.");
    const notes = [...dates.querySelectorAll("input[name=note]")].map((input) => input.value.trim() || null);
    const options = [...dates.querySelectorAll("input[name=starts_at]")].map((input, index) => ({ starts_at: new Date(input.value).toISOString(), note: notes[index] }));
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    errorBox.textContent = "";
    try {
      const vote = await api("/votes", {
        method: "POST",
        json: {
          title: data.get("title"),
          description: data.get("description") || null,
          section_ids: sectionIds,
          options,
          allow_multiple: data.get("multiple") === "yes",
          anonymous: data.get("anonymous") === "yes",
          results_visibility: data.get("visibility"),
        },
      });
      toast(`Vote started. ${vote.eligible} ${vote.eligible === 1 ? "student was" : "students were"} notified.`);
      votesPage(main, vote.id);
    } catch (error) {
      errorBox.textContent = error.message;
      button.disabled = false;
    }
  }

  return el(
    "section",
    { class: "panel", style: "margin-top:20px", "aria-labelledby": "vote-form-title" },
    el(
      "form",
      { class: "vote-form", onsubmit: submit },
      el("h2", { id: "vote-form-title" }, "New vote"),
      el("label", { class: "field" }, el("span", {}, "What is it for?"), el("input", { type: "text", name: "title", required: true, maxlength: 200, placeholder: "Databases exam" })),
      el("label", { class: "field" }, el("span", {}, "Details ", el("span", { class: "hint" }, "(optional)")), el("textarea", { name: "description", maxlength: 2000 })),
      el(
        "fieldset",
        { class: "choice-group" },
        el("legend", {}, "Classes that vote"),
        state.sections.length
          ? el("div", { class: "vote-classes" }, state.sections.map((section) => el("label", { class: "toggle" }, el("input", { type: "checkbox", name: "section", value: section.id }), section.name)))
          : el("p", { class: "muted" }, "Create a class first."),
      ),
      el("div", { class: "field" }, el("span", {}, "Dates to choose from"), dates, el("div", {}, el("button", { class: "btn secondary small", type: "button", onclick: () => addDate() }, "Add a date"))),
      radios("multiple", "Each student picks", [["no", "One date"], ["yes", "Every date that works"]], "no"),
      radios("anonymous", "Who picked what", [["no", "Show me the names"], ["yes", "Anonymous, totals only"]], "no"),
      radios("visibility", "Students see the results", [["live", "After they vote"], ["after_close", "After the vote closes"], ["professor_only", "Never"]], "after_close"),
      errorBox,
      el("div", { class: "row" }, el("button", { class: "btn", type: "submit" }, "Start vote"), el("button", { class: "btn secondary", type: "button", onclick: onCancel }, "Cancel")),
    ),
  );
}

function voteDetail(main, vote) {
  const settings = [
    vote.allow_multiple ? "Students pick every date that works." : "Students pick one date.",
    vote.anonymous ? "Anonymous." : "Named.",
    `Students see the results ${VISIBILITY_LABEL[vote.results_visibility]}.`,
  ].join(" ");
  const actions = el("div", { class: "row", style: "margin-top:20px" });

  if (vote.status === "open") {
    const leader = leadingOption(vote);
    const select = el(
      "select",
      { "aria-label": "Date to set", style: "width:auto" },
      vote.options.map((option) => el("option", { value: option.id, selected: option.id === leader.id }, `${optionText(option)}, ${option.votes} ${option.votes === 1 ? "vote" : "votes"}`)),
    );
    async function closeVote() {
      const option = vote.options.find((item) => item.id === select.value);
      const ok = await confirmDialog({
        title: "Close the vote and set the date?",
        body: `Students can no longer change their choice, and everyone in the classes is told the date: ${optionText(option)}.`,
        confirmLabel: "Set the date",
      });
      if (!ok) return;
      try {
        await api(`/votes/${vote.id}/close`, { method: "POST", json: { option_id: option.id } });
        toast("Date set. Students have been notified.");
        votesPage(main, vote.id);
      } catch (error) {
        toast(error.message);
      }
    }
    actions.append(select, el("button", { class: "btn", onclick: closeVote }, "Close vote and set date"));
  }

  async function remove() {
    const ok = await confirmDialog({
      title: "Delete this vote?",
      body: vote.status === "open" ? "The students' choices are deleted and they get a notification that the vote was cancelled." : "The vote and its results are deleted.",
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
  actions.append(el("button", { class: "btn danger", onclick: remove }, "Delete"));

  fill(main,
    el("div", {}, el("button", { class: "btn link", onclick: () => votesPage(main) }, "All votes")),
    el(
      "div",
      { class: "page-head", style: "margin-top:12px" },
      el(
        "div",
        {},
        el("h1", {}, vote.title),
        el("p", { class: "muted" }, `${vote.voted} of ${vote.eligible} students answered. ${settings}`),
        el("span", { class: "all-student-classes" }, vote.groups.map((group) => el("span", { class: "class-tag" }, group.name))),
      ),
    ),
    vote.description ? el("p", { style: "margin-bottom:16px" }, vote.description) : null,
    optionBars(vote, { showVoters: true }),
    actions,
    vote.not_voted?.length
      ? el(
          "details",
          { style: "margin-top:20px" },
          el("summary", {}, `${vote.not_voted.length} ${vote.not_voted.length === 1 ? "student has" : "students have"} not answered`),
          el("p", { class: "muted small", style: "margin-top:8px" }, vote.not_voted.map(fullName).join(", ")),
        )
      : null,
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
    const open = votes.filter((vote) => vote.status === "open");
    const closed = votes.filter((vote) => vote.status !== "open");
    if (!votes.length) return fill(host);
    fill(host,
      el("h2", {}, "Votes"),
      open.length ? el("div", { class: "stack" }, open.map(openVote)) : el("p", { class: "muted small" }, "No open votes right now."),
      closed.length
        ? el("details", { style: "margin-top:16px" }, el("summary", {}, `Decided (${closed.length})`), el("div", { class: "stack", style: "margin-top:12px" }, closed.map(closedVote)))
        : null,
    );
  }

  function header(vote) {
    return [
      el("div", { class: "row spread" }, el("h3", {}, vote.title), el("span", { class: "all-student-classes" }, vote.groups.map((group) => el("span", { class: "class-tag" }, group.name)))),
      vote.description ? el("p", {}, vote.description) : null,
    ];
  }

  function openVote(vote) {
    const type = vote.allow_multiple ? "checkbox" : "radio";
    const picked = new Set(vote.my_choice);
    const errorBox = el("p", { class: "error-text", role: "alert" });
    const name = `vote-${vote.id}`;

    async function save(event) {
      event.preventDefault();
      const optionIds = [...event.target.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
      if (!optionIds.length) return (errorBox.textContent = vote.allow_multiple ? "Tick at least one date." : "Pick a date.");
      const button = event.target.querySelector("button[type=submit]");
      button.disabled = true;
      errorBox.textContent = "";
      try {
        await api(`/votes/${vote.id}/choice`, { method: "PUT", json: { option_ids: optionIds } });
        toast("Your choice is saved. You can change it until the vote closes.");
        editing = false;
        load();
      } catch (error) {
        errorBox.textContent = error.message;
        button.disabled = false;
      }
    }

    return el(
      "form",
      { class: "panel vote-form", onsubmit: save, onchange: () => (editing = true) },
      header(vote),
      el(
        "fieldset",
        { class: "vote-picks" },
        el("legend", { class: "muted small" }, vote.allow_multiple ? "Tick every date that works for you" : "Pick the date that suits you best"),
        vote.options.map((option) =>
          el(
            "label",
            { class: "vote-pick" },
            el("input", { type, name, value: option.id, checked: picked.has(option.id) }),
            el("span", {}, optionText(option)),
            option.votes === null ? null : el("span", { class: "muted small" }, `${option.votes} ${option.votes === 1 ? "vote" : "votes"}`),
          ),
        ),
      ),
      el("p", { class: "muted small" }, picked.size ? "You have answered. Pick again to change your choice." : "You haven't answered yet."),
      errorBox,
      el("div", {}, el("button", { class: "btn", type: "submit" }, picked.size ? "Change my choice" : "Save my choice")),
    );
  }

  function closedVote(vote) {
    const chosen = vote.options.find((option) => option.id === vote.chosen_option_id);
    return el(
      "div",
      { class: "panel" },
      header(vote),
      el("p", {}, el("strong", {}, "Date: "), chosen ? optionText(chosen) : "not set"),
      vote.results_visible ? el("div", { style: "margin-top:12px" }, optionBars(vote)) : null,
    );
  }

  load().catch(() => {});
  return () => load().catch(() => {});
}
