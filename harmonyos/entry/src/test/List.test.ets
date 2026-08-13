import transcriptServiceTest from './TranscriptService.test';
import remoteErrorMapperTest from './RemoteErrorMapper.test';
import memoryNoteRepositoryTest from './MemoryNoteRepository.test';
import llmConfigViewModelTest from './LlmConfigViewModel.test';
import noteLibraryViewModelTest from './NoteLibraryViewModel.test';
import manageNotesUseCaseTest from './ManageNotesUseCase.test';
import incomingShareParserTest from './IncomingShareParser.test';
import appBootstrapUseCaseTest from './AppBootstrapUseCase.test';
import latestOperationGateTest from './LatestOperationGate.test';
import memoryLlmProfileRepositoryTest from './MemoryLlmProfileRepository.test';
import llmCompatibilityTest from './LlmCompatibility.test';
import generationTaskRunnerTest from './GenerationTaskRunner.test';

export default function testsuite(): void {
  transcriptServiceTest();
  remoteErrorMapperTest();
  memoryNoteRepositoryTest();
  llmConfigViewModelTest();
  noteLibraryViewModelTest();
  manageNotesUseCaseTest();
  incomingShareParserTest();
  appBootstrapUseCaseTest();
  latestOperationGateTest();
  memoryLlmProfileRepositoryTest();
  llmCompatibilityTest();
  generationTaskRunnerTest();
}
