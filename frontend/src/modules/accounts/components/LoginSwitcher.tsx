import React from 'react';
import { Segmented } from 'antd';

export type LoginMode = 'internal' | 'externalOtp';

interface Props {
  mode: LoginMode;
  onChange: (mode: LoginMode) => void;
}

const LoginSwitcher: React.FC<Props> = ({ mode, onChange }) => {
  return (
    <div aria-label="Login method switcher">
      <Segmented
        block
        value={mode}
        options={[
          { label: 'Internal Login', value: 'internal' },
          { label: 'Guest Login', value: 'externalOtp' },
        ]}
        onChange={(value) => onChange(value as LoginMode)}
      />
    </div>
  );
};

export default LoginSwitcher;
