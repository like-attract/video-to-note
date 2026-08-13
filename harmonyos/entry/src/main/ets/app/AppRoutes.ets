export class AppRoute {
  static readonly Login: string = 'Login';
  static readonly Subtitle: string = 'Subtitle';
  static readonly Llm: string = 'Llm';
  static readonly Processing: string = 'Processing';
  static readonly Note: string = 'Note';
  static readonly Settings: string = 'Settings';
}

export class NoteRouteParam {
  noteId: string;
  constructor(noteId: string) { this.noteId = noteId; }
}
