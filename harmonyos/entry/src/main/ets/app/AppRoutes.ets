export class AppRoute {
  static readonly Login: string = 'Login';
  static readonly Subtitle: string = 'Subtitle';
  static readonly Processing: string = 'Processing';
  static readonly Note: string = 'Note';
  static readonly Settings: string = 'Settings';
  static readonly Tasks: string = 'Tasks';
}

export class NoteRouteParam {
  noteId: string;
  constructor(noteId: string) { this.noteId = noteId; }
}

export class TaskRouteParam {
  taskId: string;
  constructor(taskId: string) { this.taskId = taskId; }
}
