import abilityTest from './Ability.test';
import huksSecretCipherTest from './HuksSecretCipher.test';
import persistenceTest from './Persistence.test';

export default function testsuite() {
  abilityTest();
  huksSecretCipherTest();
  persistenceTest();
}
