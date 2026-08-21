import fs from "node:fs";

const apiBase = "https://api.github.com";
const notionBase = "https://api.notion.com/v1";
const githubToken = required("GITHUB_TOKEN");
const projectToken = required("GH_PROJECT_TOKEN");
const notionToken = required("NOTION_API_TOKEN");
const repository = required("GITHUB_REPOSITORY");
const [repoOwner, repoName] = repository.split("/");
const eventName = process.env.GITHUB_EVENT_NAME || "schedule";
const event = readEvent();

const config = {
  owner: required("GITHUB_PROJECT_OWNER"),
  projectNumber: Number(required("GITHUB_PROJECT_NUMBER")),
  modules: required("NOTION_MODULES_DATA_SOURCE_ID"),
  workItems: required("NOTION_WORK_ITEMS_DATA_SOURCE_ID"),
  identityMap: required("NOTION_IDENTITY_MAP_DATA_SOURCE_ID"),
};

const relevantActions = new Set([
  "opened",
  "edited",
  "assigned",
  "unassigned",
  "labeled",
  "unlabeled",
  "closed",
  "reopened",
]);

if (eventName === "issues" && !relevantActions.has(event.action)) {
  console.log(`Ignoring issues action: ${event.action}`);
  process.exit(0);
}

const issues = await loadIssues();
for (const issue of issues) {
  await syncIssue(issue);
}

async function syncIssue(issue) {
  if (issue.pull_request) return;

  const moduleId = parseModuleId(issue);
  const modulePage = moduleId ? await findModule(moduleId) : null;
  const assignees = (issue.assignees || []).map((person) => person.login);
  const mappedPeople = await findMappedPeople(assignees);
  const projectItem = await ensureProjectItem(issue);
  const workflowStatus = projectItem.status || initialWorkflowStatus(issue);
  if (!projectItem.status && workflowStatus) {
    await setProjectStatus(projectItem.itemId, workflowStatus);
  }

  const syncHealth = !modulePage
    ? "Module not mapped"
    : assignees.length > mappedPeople.length
      ? "Needs identity mapping"
      : "Healthy";

  const properties = {
    Name: title(issue.title),
    Source: select("GitHub Issue"),
    "GitHub Repository": richText(repository),
    "GitHub Issue Number": { number: issue.number },
    "GitHub Issue URL": { url: issue.html_url },
    "GitHub Node ID": richText(issue.node_id),
    "GitHub Project Item ID": richText(projectItem.itemId),
    "GitHub State": select(issue.state.toUpperCase()),
    "GitHub Assignees": richText(assignees.join(", ")),
    "GitHub Description": richText(issue.body || ""),
    "GitHub Workflow Status": select(workflowStatus),
    "Sync Health": select(syncHealth),
    "Notion Owner": { people: mappedPeople.map((id) => ({ id })) },
    "GitHub Updated At": { date: { start: issue.updated_at } },
    "Last Synced At": { date: { start: new Date().toISOString() } },
  };
  properties.Module = modulePage ? { relation: [{ id: modulePage.id }] } : { relation: [] };

  const existing = await findWorkItem(issue.number);
  if (existing) {
    await notionRequest(`/pages/${existing.id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties }),
    });
    console.log(`Updated Notion work item for #${issue.number}`);
  } else {
    await notionRequest("/pages", {
      method: "POST",
      body: JSON.stringify({
        parent: { data_source_id: config.workItems },
        properties,
      }),
    });
    console.log(`Created Notion work item for #${issue.number}`);
  }
}

async function loadIssues() {
  if (eventName === "issues" && event.issue) return [event.issue];
  const requested = process.env.ISSUE_NUMBER_INPUT;
  if (requested) {
    return [await githubRequest(`/repos/${repository}/issues/${Number(requested)}`)];
  }
  const all = [];
  for (let page = 1; page <= 10; page += 1) {
    const batch = await githubRequest(
      `/repos/${repository}/issues?state=all&per_page=100&page=${page}`,
    );
    all.push(...batch.filter((issue) => !issue.pull_request));
    if (batch.length < 100) break;
  }
  return all;
}

async function findModule(moduleId) {
  const response = await notionRequest(`/data_sources/${config.modules}/query`, {
    method: "POST",
    body: JSON.stringify({
      filter: { property: "Module ID", title: { equals: moduleId } },
      page_size: 1,
    }),
  });
  return response.results?.[0] || null;
}

async function findWorkItem(issueNumber) {
  const response = await notionRequest(`/data_sources/${config.workItems}/query`, {
    method: "POST",
    body: JSON.stringify({
      filter: {
        and: [
          { property: "GitHub Repository", rich_text: { equals: repository } },
          { property: "GitHub Issue Number", number: { equals: issueNumber } },
        ],
      },
      page_size: 1,
    }),
  });
  return response.results?.[0] || null;
}

async function findMappedPeople(logins) {
  const ids = [];
  for (const login of logins) {
    const response = await notionRequest(`/data_sources/${config.identityMap}/query`, {
      method: "POST",
      body: JSON.stringify({
        filter: { property: "GitHub Login", title: { equals: login } },
        page_size: 1,
      }),
    });
    const personProperty = response.results?.[0]?.properties?.["Notion Person"];
    for (const person of personProperty?.people || []) ids.push(person.id);
  }
  return ids;
}

async function ensureProjectItem(issue) {
  let project = await projectQuery();
  let item = project.items.find((candidate) => candidate.content?.id === issue.node_id);
  if (!item) {
    const mutation = await githubGraphql(
      `mutation($projectId: ID!, $contentId: ID!) {
        addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
          item { id }
        }
      }`,
      { projectId: project.id, contentId: issue.node_id },
    );
    const addedItemId = mutation.addProjectV2ItemById.item.id;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      project = await projectQuery();
      item = project.items.find((candidate) => candidate.id === addedItemId);
      if (item) break;
      await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
    }
    if (!item) throw new Error(`Project item ${addedItemId} was not visible after creation`);
  }
  const statusField = project.fields.find((field) => field.name === "Workflow Status");
  const statusValue = (item.fieldValues || []).find((value) => value.field?.id === statusField?.id);
  return {
    itemId: item.id,
    status: statusValue?.name || null,
    statusField,
  };
}

async function projectQuery() {
  const metadata = await githubGraphql(
    `query($owner: String!, $number: Int!) {
      organization(login: $owner) {
        projectV2(number: $number) {
          id
          fields(first: 100) {
            nodes {
              ... on ProjectV2SingleSelectField { id name options { id name } }
            }
          }
        }
      }
    }`,
    { owner: config.owner, number: config.projectNumber },
  );
  const project = metadata.organization?.projectV2;
  if (!project) throw new Error("Pilot GitHub Project was not found");

  const items = [];
  let after = null;
  do {
    const page = await githubGraphql(
      `query($owner: String!, $number: Int!, $after: String) {
        organization(login: $owner) {
          projectV2(number: $number) {
            items(first: 100, after: $after) {
              pageInfo { hasNextPage endCursor }
              nodes {
                id
                content { ... on Issue { id number repository { nameWithOwner } } }
                fieldValues(first: 50) {
                  nodes {
                    ... on ProjectV2ItemFieldSingleSelectValue {
                      name
                      optionId
                      field { ... on ProjectV2SingleSelectField { id name } }
                    }
                  }
                }
              }
            }
          }
        }
      }`,
      { owner: config.owner, number: config.projectNumber, after },
    );
    const pageItems = page.organization.projectV2.items;
    items.push(...pageItems.nodes.map((item) => ({ ...item, fieldValues: item.fieldValues.nodes })));
    after = pageItems.pageInfo.hasNextPage ? pageItems.pageInfo.endCursor : null;
  } while (after);

  return { ...project, fields: project.fields.nodes, items };
}

async function setProjectStatus(itemId, status) {
  const project = await projectQuery();
  const field = project.fields.find((candidate) => candidate.name === "Workflow Status");
  const option = field?.options.find((candidate) => candidate.name === status);
  if (!field || !option) throw new Error(`Unknown Workflow Status option: ${status}`);
  await githubGraphql(
    `mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId,
        itemId: $itemId,
        fieldId: $fieldId,
        value: {singleSelectOptionId: $optionId}
      }) { projectV2Item { id } }
    }`,
    { projectId: project.id, itemId, fieldId: field.id, optionId: option.id },
  );
}

async function githubRequest(path, options = {}) {
  return request(`${apiBase}${path}`, {
    ...options,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${githubToken}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(options.headers || {}),
    },
  });
}

async function githubGraphql(query, variables) {
  const response = await request("https://api.github.com/graphql", {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${projectToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query, variables }),
  });
  if (response.errors?.length) {
    throw new Error(`GitHub GraphQL error: ${JSON.stringify(response.errors)}`);
  }
  return response.data;
}

async function notionRequest(path, options = {}) {
  return request(`${notionBase}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${notionToken}`,
      "Notion-Version": process.env.NOTION_VERSION || "2025-09-03",
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
}

async function request(url, options) {
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    const response = await fetch(url, options);
    const text = await response.text();
    let body;
    try { body = text ? JSON.parse(text) : {}; } catch { body = { raw: text }; }
    if (response.ok) return body;
    if (![408, 409, 429, 500, 502, 503, 504].includes(response.status) || attempt === 4) {
      throw new Error(`${url} ${response.status}: ${JSON.stringify(body)}`);
    }
    await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
  }
}

function parseModuleId(issue) {
  const label = issue.labels
    ?.map((item) => typeof item === "string" ? item : item.name)
    .find((name) => /^module:[A-Z][A-Z0-9_-]*$/i.test(name || ""));
  if (label) return label.slice("module:".length).toUpperCase();
  const match = issue.body?.match(/### Module(?: ID)?\s+([^\n\r]+)/i);
  return match?.[1]?.trim().toUpperCase() || null;
}

function initialWorkflowStatus(issue) {
  const match = issue.body?.match(/### Initial workflow status\s+([^\n\r]+)/i);
  const value = match?.[1]?.trim();
  return ["Backlog", "In progress", "Blocked", "Done"].includes(value) ? value : "Backlog";
}

function title(value) { return { title: [{ type: "text", text: { content: value.slice(0, 2000) } }] }; }
function select(name) { return { select: name ? { name } : null }; }
function richText(value) {
  const text = String(value || "");
  const chunks = [];
  for (let i = 0; i < text.length; i += 1900) chunks.push({ type: "text", text: { content: text.slice(i, i + 1900) } });
  return { rich_text: chunks };
}
function readEvent() {
  const path = process.env.GITHUB_EVENT_PATH;
  return path && fs.existsSync(path) ? JSON.parse(fs.readFileSync(path, "utf8")) : {};
}
function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}
