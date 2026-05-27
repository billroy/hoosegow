(function () {
  const COMMAND_PREFIX = '>';

  function splitQuickCreateText(text) {
    const raw = String(text || '').trim();
    if (!raw) return { title: '', description: '' };
    const slashIdx = raw.indexOf('/');
    return slashIdx >= 0
      ? {
          title: raw.slice(0, slashIdx).trim(),
          description: raw.slice(slashIdx + 1).trim(),
        }
      : { title: raw, description: '' };
  }

  function parseCommandInput(input) {
    const raw = String(input || '').trim();
    const text = raw.startsWith(COMMAND_PREFIX) ? raw.slice(1).trim() : raw;
    const spaceIdx = text.indexOf(' ');
    return {
      raw,
      text,
      command: (spaceIdx >= 0 ? text.slice(0, spaceIdx) : text).toLowerCase(),
      args: spaceIdx >= 0 ? text.slice(spaceIdx + 1).trim() : '',
    };
  }

  function normalize(value) {
    return String(value || '').trim().toLowerCase();
  }

  function requireWorkspace(ctx) {
    return ctx?.activeWorkspaceId ? true : 'Open a project first';
  }

  function command(definition) {
    return {
      aliases: [],
      keywords: [],
      shortcut: '',
      prefixes: [COMMAND_PREFIX],
      ...definition,
    };
  }

  function buildCommands(ctx = {}) {
    const themes = Array.isArray(ctx.themes) ? ctx.themes : [];
    const ambientPresets = Array.isArray(ctx.ambientPresets) ? ctx.ambientPresets : [];

    const commands = [
      command({
        id: 'ticket.create',
        title: 'Create Ticket',
        subtitle: 'Create a new ticket in the active project',
        group: 'Tickets',
        aliases: ['new', 'ticket', 'create', 'new ticket'],
        keywords: ['issue', 'task', 'todo'],
        shortcut: 'Enter',
        available: requireWorkspace,
        run(runCtx, args) {
          const payload = splitQuickCreateText(args);
          if (!payload.title) {
            runCtx.actions.openCreateModal();
            return;
          }
          runCtx.actions.quickCreateTask(payload);
        },
      }),
      command({
        id: 'ticket.archive_done',
        title: 'Archive Done Tickets',
        subtitle: 'Archive all tickets in Done after confirmation',
        group: 'Tickets',
        aliases: ['archive done', 'archive'],
        keywords: ['clean up', 'done'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.archiveDone(),
      }),
      command({
        id: 'tickets.scope.live',
        title: 'Show Live Tickets',
        subtitle: 'Switch the ticket list to live tickets',
        group: 'Tickets',
        aliases: ['live', 'scope live', 'show live'],
        available: requireWorkspace,
        run(runCtx) {
          runCtx.actions.setTicketListScope('live');
          runCtx.actions.setActiveTab('tasks');
        },
      }),
      command({
        id: 'tickets.scope.archived',
        title: 'Show Archived Tickets',
        subtitle: 'Switch the ticket list to archived tickets',
        group: 'Tickets',
        aliases: ['archived', 'archive list', 'scope archived'],
        available: requireWorkspace,
        run(runCtx) {
          runCtx.actions.setTicketListScope('archived');
          runCtx.actions.setActiveTab('tasks');
        },
      }),
      command({
        id: 'tickets.scope',
        title: 'Set Ticket Scope',
        subtitle: 'Use live or archived',
        group: 'Tickets',
        aliases: ['scope'],
        available: requireWorkspace,
        run(runCtx, args) {
          const scope = normalize(args);
          if (scope !== 'live' && scope !== 'archived') {
            runCtx.actions.addToast('Usage: >scope live|archived', 'error');
            return;
          }
          runCtx.actions.setTicketListScope(scope);
          runCtx.actions.setActiveTab('tasks');
        },
      }),
      command({
        id: 'tickets.view.kanban',
        title: 'Tickets: Kanban View',
        subtitle: 'Show tickets as columns',
        group: 'Views',
        aliases: ['kanban', 'view kanban'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.setTicketsViewMode('kanban'),
      }),
      command({
        id: 'tickets.view.list',
        title: 'Tickets: List View',
        subtitle: 'Show tickets as a searchable list',
        group: 'Views',
        aliases: ['list', 'view list'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.setTicketsViewMode('list'),
      }),
      command({
        id: 'tickets.view',
        title: 'Set Ticket View',
        subtitle: 'Use kanban or list',
        group: 'Views',
        aliases: ['view'],
        available: requireWorkspace,
        run(runCtx, args) {
          const mode = normalize(args);
          if (mode !== 'kanban' && mode !== 'list') {
            runCtx.actions.addToast('Usage: >view kanban|list', 'error');
            return;
          }
          runCtx.actions.setTicketsViewMode(mode);
        },
      }),
      command({
        id: 'tab.open',
        title: 'Open Tab',
        subtitle: 'Use tickets, workers, files, commits, or chat',
        group: 'Views',
        aliases: ['tab', 'open tab'],
        keywords: ['navigation'],
        available: requireWorkspace,
        run(runCtx, args) {
          if (!runCtx.actions.openStandardTab(args)) {
            runCtx.actions.addToast(`Unknown tab "${args}"`, 'error');
          }
        },
      }),
      command({
        id: 'tab.tickets',
        title: 'Open Tickets',
        subtitle: 'Switch to the Tickets tab',
        group: 'Views',
        aliases: ['tickets', 'tasks', 'open tickets'],
        keywords: ['tab', 'navigation'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.setActiveTab('tasks'),
      }),
      command({
        id: 'tab.workers',
        title: 'Open Workers',
        subtitle: 'Switch to the Workers tab',
        group: 'Views',
        aliases: ['workers', 'bullpen', 'open workers'],
        keywords: ['tab', 'navigation'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.setActiveTab('workers'),
      }),
      command({
        id: 'tab.files',
        title: 'Open Files',
        subtitle: 'Switch to the Files tab',
        group: 'Views',
        aliases: ['files', 'open files'],
        keywords: ['tab', 'navigation'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.setActiveTab('files'),
      }),
      command({
        id: 'tab.commits',
        title: 'Open Commits',
        subtitle: 'Switch to the Commits tab',
        group: 'Views',
        aliases: ['commits', 'git', 'open commits'],
        keywords: ['tab', 'navigation'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.setActiveTab('commits'),
      }),
      command({
        id: 'tab.chat',
        title: 'Open Live Agent Chat',
        subtitle: 'Switch to or create a live agent chat tab',
        group: 'Chat',
        aliases: ['chat', 'live', 'open chat'],
        keywords: ['tab', 'navigation'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.openChatTab(),
      }),
      command({
        id: 'chat.new',
        title: 'New Live Agent Chat',
        subtitle: 'Create a new live agent chat tab',
        group: 'Chat',
        aliases: ['new chat', 'add chat'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.addLiveAgentTab({ activate: true }),
      }),
      command({
        id: 'pane.left.toggle',
        title: 'Toggle Left Pane',
        subtitle: 'Show or hide the project and queue pane',
        group: 'Views',
        aliases: ['left', 'pane', 'toggle left pane', 'toggle-left-pane'],
        run: runCtx => runCtx.actions.toggleLeftPane(),
      }),
      command({
        id: 'columns.manage',
        title: 'Manage Columns',
        subtitle: 'Open the column manager',
        group: 'Columns',
        aliases: ['columns', 'manage columns'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.openColumnManager(),
      }),
      command({
        id: 'project.export',
        title: 'Export Project',
        subtitle: 'Download the active project as a zip',
        group: 'Project',
        aliases: ['export project', 'export workspace'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.exportWorkspace(),
      }),
      command({
        id: 'workers.export',
        title: 'Export Workers',
        subtitle: 'Download worker configuration for the active project',
        group: 'Project',
        aliases: ['export workers'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.exportWorkers(),
      }),
      command({
        id: 'project.export_all',
        title: 'Export All',
        subtitle: 'Download all Bullpen project data as a zip',
        group: 'Project',
        aliases: ['export all'],
        run: runCtx => runCtx.actions.exportAll(),
      }),
      command({
        id: 'project.import',
        title: 'Import Project',
        subtitle: 'File picker command support is pending',
        group: 'Project',
        aliases: ['import project', 'import workspace'],
        available: () => 'Use the main menu for imports until palette file pickers land',
        run: () => {},
      }),
      command({
        id: 'workers.import',
        title: 'Import Workers',
        subtitle: 'File picker command support is pending',
        group: 'Project',
        aliases: ['import workers'],
        available: () => 'Use the main menu for imports until palette file pickers land',
        run: () => {},
      }),
      command({
        id: 'project.import_all',
        title: 'Import All',
        subtitle: 'File picker command support is pending',
        group: 'Project',
        aliases: ['import all'],
        available: () => 'Use the main menu for imports until palette file pickers land',
        run: () => {},
      }),
      command({
        id: 'theme.change',
        title: 'Change Theme',
        subtitle: 'Use a theme id or label',
        group: 'Preferences',
        aliases: ['theme', 'set theme'],
        run(runCtx, args) {
          const query = normalize(args);
          if (!query) {
            runCtx.actions.addToast(`Themes: ${themes.map(t => t.id).join(', ')}`);
            return;
          }
          const match = themes.find(t => normalize(t.id) === query || normalize(t.label) === query);
          if (!match) {
            runCtx.actions.addToast(`Unknown theme "${args}"`, 'error');
            return;
          }
          runCtx.actions.setTheme(match.id);
        },
      }),
      command({
        id: 'ambient.change',
        title: 'Set Ambient Sound',
        subtitle: 'Use an ambient preset id or off',
        group: 'Preferences',
        aliases: ['ambient', 'sound', 'set ambient'],
        available: requireWorkspace,
        run(runCtx, args) {
          const query = normalize(args);
          if (!query || query === 'off' || query === 'none') {
            runCtx.actions.setAmbientPreset('');
            return;
          }
          const match = ambientPresets.find(p => normalize(p.key) === query || normalize(p.label) === query);
          if (!match) {
            runCtx.actions.addToast(`Unknown ambient preset "${args}"`, 'error');
            return;
          }
          runCtx.actions.setAmbientPreset(match.key);
        },
      }),
      command({
        id: 'ambient.off',
        title: 'Turn Ambient Sound Off',
        subtitle: 'Disable ambient background sound',
        group: 'Preferences',
        aliases: ['ambient off', 'sound off'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.setAmbientPreset(''),
      }),
      command({
        id: 'volume.set',
        title: 'Set Ambient Volume',
        subtitle: 'Use a number from 0 to 100',
        group: 'Preferences',
        aliases: ['volume', 'set volume'],
        available: requireWorkspace,
        run(runCtx, args) {
          const value = Number(args);
          if (!Number.isFinite(value)) {
            runCtx.actions.addToast('Usage: >volume 0-100', 'error');
            return;
          }
          runCtx.actions.setAmbientVolume(value);
        },
      }),
      command({
        id: 'help.commands',
        title: 'Show Commands',
        subtitle: 'Open command mode and browse available commands',
        group: 'Help',
        aliases: ['help', 'commands'],
        run: runCtx => runCtx.actions.addToast('Type > and start typing to search commands.'),
      }),
      command({
        id: 'project.copy_path',
        title: 'Copy Project Name',
        subtitle: 'Copy the active project name to the clipboard',
        group: 'Help',
        aliases: ['copy path', 'workspace path'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.copyText('Project name', runCtx.projectPath),
      }),
      command({
        id: 'project.copy_id',
        title: 'Copy Project ID',
        subtitle: 'Copy the active project id to the clipboard',
        group: 'Help',
        aliases: ['copy project id', 'project id'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.copyText('Project ID', runCtx.activeWorkspaceId),
      }),
    ];

    for (const theme of themes) {
      commands.push(command({
        id: `theme.${theme.id}`,
        title: `Theme: ${theme.label}`,
        subtitle: `Switch to ${theme.label}`,
        group: 'Preferences',
        aliases: [theme.id, theme.label, `${theme.label} theme`],
        keywords: ['theme', 'color'],
        run: runCtx => runCtx.actions.setTheme(theme.id),
      }));
    }

    for (const preset of ambientPresets) {
      commands.push(command({
        id: `ambient.${preset.key}`,
        title: `Ambient: ${preset.label}`,
        subtitle: `Switch ambient sound to ${preset.label}`,
        group: 'Preferences',
        aliases: [preset.key, preset.label, `${preset.label} ambient`],
        keywords: ['ambient', 'sound'],
        available: requireWorkspace,
        run: runCtx => runCtx.actions.setAmbientPreset(preset.key),
      }));
    }

    return commands.map(definition => {
      let disabledReason = '';
      if (typeof definition.available === 'function') {
        const available = definition.available(ctx);
        if (available !== true) disabledReason = available || 'Unavailable';
      }
      return { ...definition, disabledReason };
    });
  }

  function searchableText(command) {
    return [
      command.id,
      command.title,
      command.subtitle,
      command.group,
      ...(command.aliases || []),
      ...(command.keywords || []),
    ].map(normalize).join(' ');
  }

  function scoreCommand(command, query) {
    const q = normalize(query);
    if (!q) return command.disabledReason ? 5 : 10;
    const haystack = searchableText(command);
    const title = normalize(command.title);
    const aliases = (command.aliases || []).map(normalize);
    const tokens = q.split(/\s+/).filter(Boolean);
    let score = command.disabledReason ? -5 : 0;
    for (const token of tokens) {
      if (aliases.includes(token) || normalize(command.id) === token) score += 100;
      else if (title === token) score += 90;
      else if (title.startsWith(token)) score += 60;
      else if (aliases.some(alias => alias.startsWith(token))) score += 55;
      else if (haystack.includes(token)) score += 20;
      else return 0;
    }
    return score;
  }

  function filterCommands(commands, query, limit = 12) {
    return (commands || [])
      .map(cmd => ({ cmd, score: scoreCommand(cmd, query) }))
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score || a.cmd.title.localeCompare(b.cmd.title))
      .slice(0, limit)
      .map(item => item.cmd);
  }

  function commandMatches(command, token) {
    const needle = normalize(token);
    if (!needle) return false;
    return normalize(command.id) === needle
      || normalize(command.title) === needle
      || (command.aliases || []).some(alias => normalize(alias) === needle);
  }

  function findCommand(commands, input) {
    const parsed = parseCommandInput(input);
    if (!parsed.command) return null;
    const exact = (commands || []).find(cmd => commandMatches(cmd, parsed.command));
    if (exact) return { command: exact, args: parsed.args, parsed };
    const fuzzy = filterCommands(commands, parsed.text, 1)[0];
    return fuzzy ? { command: fuzzy, args: parsed.args, parsed } : null;
  }

  window.BullpenCommands = {
    COMMAND_PREFIX,
    splitQuickCreateText,
    parseCommandInput,
    buildCommands,
    filterCommands,
    findCommand,
  };
})();
