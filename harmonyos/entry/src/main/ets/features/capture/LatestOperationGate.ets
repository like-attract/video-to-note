export class LatestOperationGate {
  private version: number = 0;

  begin(): number {
    this.version += 1;
    return this.version;
  }

  isCurrent(version: number): boolean { return version === this.version; }

  invalidate(): void { this.version += 1; }
}
