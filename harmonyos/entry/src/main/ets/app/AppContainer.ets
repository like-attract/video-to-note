import { common } from '@kit.AbilityKit';
import type { BiliSessionRepository } from '../domain/auth/BiliSessionRepository';
import type { GenerationRepository } from '../domain/generation/GenerationRepository';
import type { GenerationTaskRepository } from '../domain/generation/GenerationTaskRepository';
import type { NoteRepository } from '../domain/notes/NoteRepository';
import type { LlmProfileRepository } from '../domain/generation/LlmProfileRepository';
import type { BiliAuthGateway } from '../domain/auth/BiliAuthGateway';
import type { BiliContentRepository } from '../domain/capture/BiliContentRepository';
import type { NoteGenerator } from '../domain/generation/NoteGenerator';
import { MemoryBiliSessionRepository } from '../data/memory/MemoryBiliSessionRepository';
import { MemoryGenerationRepository } from '../data/memory/MemoryGenerationRepository';
import { MemoryGenerationTaskRepository } from '../data/memory/MemoryGenerationTaskRepository';
import { MemoryNoteRepository } from '../data/memory/MemoryNoteRepository';
import { MemoryLlmProfileRepository } from '../data/memory/MemoryLlmProfileRepository';
import { RemoteBiliAuthGateway } from '../data/remote/RemoteBiliAuthGateway';
import { RemoteBiliContentRepository } from '../data/remote/RemoteBiliContentRepository';
import { RemoteNoteGenerator } from '../data/remote/RemoteNoteGenerator';
import { LoginUseCase } from '../domain/auth/LoginUseCase';
import { CaptureUseCase } from '../domain/capture/CaptureUseCase';
import { GenerateNoteUseCase } from '../domain/generation/GenerateNoteUseCase';
import { PersistentGenerationRepository } from '../data/local/PersistentGenerationRepository';
import { RdbGenerationTaskRepository } from '../data/local/RdbGenerationTaskRepository';
import { PersistentLlmProfileRepository } from '../data/local/PersistentLlmProfileRepository';
import { RdbNoteRepository } from '../data/local/RdbNoteRepository';
import { SecureBiliSessionRepository } from '../data/local/SecureBiliSessionRepository';
import { SecureSecretStore } from '../data/local/SecureSecretStore';
import { DataManagementUseCase } from '../domain/settings/DataManagementUseCase';
import { AppBootstrapUseCase } from '../domain/startup/AppBootstrapUseCase';
import { ManageNotesUseCase } from '../domain/notes/ManageNotesUseCase';
import { GenerationTaskRunner } from '../domain/generation/GenerationTaskRunner';

export class AppContainer {
  readonly biliAuth: BiliAuthGateway = new RemoteBiliAuthGateway();
  readonly biliContent: BiliContentRepository = new RemoteBiliContentRepository();
  readonly noteGenerator: NoteGenerator = new RemoteNoteGenerator();
  sessions: BiliSessionRepository = new MemoryBiliSessionRepository();
  generation: GenerationRepository = new MemoryGenerationRepository();
  generationTasks: GenerationTaskRepository = new MemoryGenerationTaskRepository();
  notes: NoteRepository = new MemoryNoteRepository();
  llmProfiles: LlmProfileRepository = new MemoryLlmProfileRepository();
  login: LoginUseCase = new LoginUseCase(this.biliAuth, this.sessions);
  capture: CaptureUseCase = new CaptureUseCase(this.biliContent, this.sessions, this.generation);
  generateNote: GenerateNoteUseCase = new GenerateNoteUseCase(
    this.noteGenerator, this.generation, this.notes);
  taskRunner: GenerationTaskRunner = new GenerationTaskRunner(
    this.generationTasks, this.llmProfiles, this.generateNote, this.notes);
  private secrets?: SecureSecretStore;
  dataManagement?: DataManagementUseCase;
  bootstrap: AppBootstrapUseCase = new AppBootstrapUseCase(this.sessions, this.generation);
  manageNotes: ManageNotesUseCase = new ManageNotesUseCase(this.notes, this.generation);

  initialize(context: common.UIAbilityContext): void {
    const secrets: SecureSecretStore = new SecureSecretStore(context);
    this.secrets = secrets;
    this.sessions = new SecureBiliSessionRepository(secrets);
    this.generation = new PersistentGenerationRepository(context, secrets);
    this.generationTasks = new RdbGenerationTaskRepository(context);
    this.llmProfiles = new PersistentLlmProfileRepository(context, secrets);
    this.notes = new RdbNoteRepository(context);
    this.rebindUseCases();
  }

  private rebindUseCases(): void {
    this.login = new LoginUseCase(this.biliAuth, this.sessions);
    this.capture = new CaptureUseCase(this.biliContent, this.sessions, this.generation);
    this.generateNote = new GenerateNoteUseCase(this.noteGenerator, this.generation, this.notes);
    this.taskRunner = new GenerationTaskRunner(
      this.generationTasks, this.llmProfiles, this.generateNote, this.notes);
    if (this.secrets !== undefined) {
      this.dataManagement = new DataManagementUseCase(
        this.sessions, this.generation, this.generationTasks, this.notes, this.llmProfiles, this.secrets);
    }
    this.bootstrap = new AppBootstrapUseCase(this.sessions, this.generation);
    this.manageNotes = new ManageNotesUseCase(this.notes, this.generation);
  }
}

export const appContainer: AppContainer = new AppContainer();
